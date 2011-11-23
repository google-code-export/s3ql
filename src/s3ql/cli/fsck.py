'''
fsck.py - this file is part of S3QL (http://s3ql.googlecode.com)

Copyright (C) 2008-2009 Nikolaus Rath <Nikolaus@rath.org>

This program can be distributed under the terms of the GNU GPLv3.
'''

from __future__ import division, print_function, absolute_import
from llfuse import ROOT_INODE
from s3ql import CURRENT_FS_REV
from s3ql.backends.common import get_bucket
from s3ql.common import (get_bucket_cachedir, cycle_metadata, setup_logging, 
    QuietError, get_seq_no, restore_metadata, dump_metadata, CTRL_INODE, 
    stream_write_bz2, stream_read_bz2, create_tables)
from s3ql.database import Connection
from s3ql.fsck import Fsck
from s3ql.parse_args import ArgumentParser
import apsw
import cPickle as pickle
import logging
import os
import stat
import sys
import tempfile
import textwrap
import time

log = logging.getLogger("fsck")

def parse_args(args):

    parser = ArgumentParser(
        description="Checks and repairs an S3QL filesystem.")

    parser.add_log('~/.s3ql/fsck.log')
    parser.add_cachedir()
    parser.add_authfile()
    parser.add_debug_modules()
    parser.add_quiet()
    parser.add_version()
    parser.add_storage_url()

    parser.add_argument("--batch", action="store_true", default=False,
                      help="If user input is required, exit without prompting.")
    parser.add_argument("--force", action="store_true", default=False,
                      help="Force checking even if file system is marked clean.")
    options = parser.parse_args(args)

    return options

def main(args=None):

    if args is None:
        args = sys.argv[1:]

    options = parse_args(args)
    setup_logging(options)
        
    # Check if fs is mounted on this computer
    # This is not foolproof but should prevent common mistakes
    match = options.storage_url + ' /'
    with open('/proc/mounts', 'r') as fh:
        for line in fh:
            if line.startswith(match):
                raise QuietError('Can not check mounted file system.')
    
    bucket = get_bucket(options)
    
    cachepath = get_bucket_cachedir(options.storage_url, options.cachedir)
    seq_no = get_seq_no(bucket)
    param_remote = bucket.lookup('s3ql_metadata')
    db = None
    
    if os.path.exists(cachepath + '.params'):
        assert os.path.exists(cachepath + '.db')
        param = pickle.load(open(cachepath + '.params', 'rb'))
        if param['seq_no'] < seq_no:
            log.info('Ignoring locally cached metadata (outdated).')
            param = bucket.lookup('s3ql_metadata')
        else:
            log.info('Using cached metadata.')
            db = Connection(cachepath + '.db')
            assert not os.path.exists(cachepath + '-cache') or param['needs_fsck']
    
        if param_remote['seq_no'] != param['seq_no']:
            log.warn('Remote metadata is outdated.')
            param['needs_fsck'] = True
            
    else:
        param = param_remote
        assert not os.path.exists(cachepath + '-cache')
        # .db might exist if mount.s3ql is killed at exactly the right instant
        # and should just be ignored.
       
    # Check revision
    if param['revision'] < CURRENT_FS_REV:
        raise QuietError('File system revision too old, please run `s3qladm upgrade` first.')
    elif param['revision'] > CURRENT_FS_REV:
        raise QuietError('File system revision too new, please update your '
                         'S3QL installation.')
    
    if param['seq_no'] < seq_no:
        if bucket.is_get_consistent():
            print(textwrap.fill(textwrap.dedent('''\
                  Up to date metadata is not available. Probably the file system has not
                  been properly unmounted and you should try to run fsck on the computer 
                  where the file system has been mounted most recently.
                  ''')))
        else:
            print(textwrap.fill(textwrap.dedent('''\
                  Up to date metadata is not available. Either the file system has not
                  been unmounted cleanly or the data has not yet propagated through the backend.
                  In the later case, waiting for a while should fix the problem, in
                  the former case you should try to run fsck on the computer where
                  the file system has been mounted most recently
                  ''')))
    
        print('Enter "continue" to use the outdated data anyway:',
              '> ', sep='\n', end='')
        if options.batch:
            raise QuietError('(in batch mode, exiting)')
        if sys.stdin.readline().strip() != 'continue':
            raise QuietError()
        
        param['seq_no'] = seq_no
        param['needs_fsck'] = True
    
    
    if (not param['needs_fsck']
        and param['max_inode'] < 2**31 
        and ((time.time() - time.timezone) - param['last_fsck'])
             < 60 * 60 * 24 * 31): # last check more than 1 month ago
        if options.force:
            log.info('File system seems clean, checking anyway.')
        else:
            log.info('File system is marked as clean. Use --force to force checking.')
            return
    
    # If using local metadata, check consistency
    if db:
        log.info('Checking DB integrity...')
        try:
            # get_list may raise CorruptError itself
            res = db.get_list('PRAGMA integrity_check(20)')
            if res[0][0] != u'ok':
                log.error('\n'.join(x[0] for x in res ))
                raise apsw.CorruptError()
        except apsw.CorruptError:
            raise QuietError('Local metadata is corrupted. Remove or repair the following '
                             'files manually and re-run fsck:\n'
                             + cachepath + '.db (corrupted)\n'
                             + cachepath + '.param (intact)')
    else:
        def do_read(fh):
            tmpfh = tempfile.TemporaryFile()
            stream_read_bz2(fh, tmpfh)
            return tmpfh
        log.info('Downloading and decompressing metadata...')
        tmpfh = bucket.perform_read(do_read, "s3ql_metadata") 
        os.close(os.open(cachepath + '.db.tmp', os.O_RDWR | os.O_CREAT | os.O_TRUNC,
                         stat.S_IRUSR | stat.S_IWUSR)) 
        db = Connection(cachepath + '.db.tmp', fast_mode=True)
        log.info("Reading metadata...")
        tmpfh.seek(0)
        restore_metadata(tmpfh, db)
        db.close()
        os.rename(cachepath + '.db.tmp', cachepath + '.db')
        db = Connection(cachepath + '.db')
    
    # Increase metadata sequence no 
    param['seq_no'] += 1
    param['needs_fsck'] = True
    bucket['s3ql_seq_no_%d' % param['seq_no']] = 'Empty'
    pickle.dump(param, open(cachepath + '.params', 'wb'), 2)
    
    fsck = Fsck(cachepath + '-cache', bucket, param, db)
    fsck.check()
    param['max_inode'] = db.get_val('SELECT MAX(id) FROM inodes')
    
    if fsck.uncorrectable_errors:
        raise QuietError("Uncorrectable errors found, aborting.")
        
    if os.path.exists(cachepath + '-cache'):
        os.rmdir(cachepath + '-cache')
        
    if param['max_inode'] >= 2**31: 
        renumber_inodes(db)
        param['inode_gen'] += 1
        param['max_inode'] = db.get_val('SELECT MAX(id) FROM inodes')
            
    cycle_metadata(bucket)
    param['needs_fsck'] = False
    param['last_fsck'] = time.time() - time.timezone
    param['last-modified'] = time.time() - time.timezone
    
    log.info('Dumping metadata...')
    fh = tempfile.TemporaryFile()
    dump_metadata(db, fh)            
    def do_write(obj_fh):
        fh.seek(0)
        stream_write_bz2(fh, obj_fh)
        return obj_fh
    
    log.info("Compressing and uploading metadata...")
    obj_fh = bucket.perform_write(do_write, "s3ql_metadata", metadata=param,
                                  is_compressed=True) 
    log.info('Wrote %.2f MB of compressed metadata.', obj_fh.get_obj_size() / 1024**2)
    pickle.dump(param, open(cachepath + '.params', 'wb'), 2)
        
    db.execute('ANALYZE')
    db.execute('VACUUM')
    db.close()      

def renumber_inodes(db):
    '''Renumber inodes'''
    
    log.info('Renumbering inodes...')
    for table in ('inodes', 'inode_blocks', 'symlink_targets',
                  'contents', 'names', 'blocks', 'objects', 'ext_attributes'):
        db.execute('ALTER TABLE %s RENAME TO %s_old' % (table, table))
    
    for table in ('contents_v', 'ext_attributes_v'):
        db.execute('DROP VIEW %s' % table)
        
    create_tables(db)
    for table in ('names', 'blocks', 'objects'):
        db.execute('DROP TABLE %s' % table)
        db.execute('ALTER TABLE %s_old RENAME TO %s' % (table, table))
    
    log.info('..mapping..')
    db.execute('CREATE TEMPORARY TABLE inode_map (rowid INTEGER PRIMARY KEY AUTOINCREMENT, id INTEGER UNIQUE)')
    db.execute('INSERT INTO inode_map (rowid, id) VALUES(?,?)', (ROOT_INODE, ROOT_INODE))
    db.execute('INSERT INTO inode_map (rowid, id) VALUES(?,?)', (CTRL_INODE, CTRL_INODE))
    db.execute('INSERT INTO inode_map (id) SELECT id FROM inodes_old WHERE id > ? ORDER BY id ASC',
               (CTRL_INODE,))

    log.info('..inodes..')    
    db.execute('INSERT INTO inodes (id,mode,uid,gid,mtime,atime,ctime,refcount,size,locked,rdev) '
               'SELECT (SELECT rowid FROM inode_map WHERE inode_map.id = inodes_old.id), '
               '       mode,uid,gid,mtime,atime,ctime,refcount,size,locked,rdev FROM inodes_old')
    
    log.info('..inode_blocks..')
    db.execute('INSERT INTO inode_blocks (inode, blockno, block_id) '
               'SELECT (SELECT rowid FROM inode_map WHERE inode_map.id = inode_blocks_old.inode), '
               '       blockno, block_id FROM inode_blocks_old')
    
    log.info('..contents..')
    db.execute('INSERT INTO contents (inode, parent_inode, name_id) '
               'SELECT (SELECT rowid FROM inode_map WHERE inode_map.id = contents_old.inode), '
               '       (SELECT rowid FROM inode_map WHERE inode_map.id = contents_old.parent_inode), '
               '       name_id FROM contents_old')
    
    log.info('..symlink_targets..')
    db.execute('INSERT INTO symlink_targets (inode, target) '
               'SELECT (SELECT rowid FROM inode_map WHERE inode_map.id = symlink_targets_old.inode), '
               '       target FROM symlink_targets_old')
    
    log.info('..ext_attributes..')        
    db.execute('INSERT INTO ext_attributes (inode, name_id, value) '
               'SELECT (SELECT rowid FROM inode_map WHERE inode_map.id = ext_attributes_old.inode), '
               '       name_id, value FROM ext_attributes_old')

    for table in ('inodes', 'inode_blocks', 'symlink_targets',
                  'contents', 'ext_attributes'):
        db.execute('DROP TABLE %s_old' % table)
       
    db.execute('DROP TABLE inode_map')

        
if __name__ == '__main__':
    main(sys.argv[1:])

