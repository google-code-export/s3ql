#!/usr/bin/env python
'''
$Id$

Copyright (C) 2008-2009 Nikolaus Rath <Nikolaus@rath.org>

This program can be distributed under the terms of the GNU LGPL.
'''

# TODO: Integrate this file into setup.py

from __future__ import division, print_function

import sys
import os
import tempfile
import subprocess
import re

# These are the definitons that we need 
export_regex = ['^FUSE_SET_.*', '^XATTR_.*', 'fuse_reply_.*' ]
export_symbols = ['fuse_mount', 'fuse_lowlevel_new', 'fuse_add_direntry',
                 'fuse_set_signal_handlers', 'fuse_session_add_chan',
                 'fuse_session_loop_mt', 'fuse_session_remove_chan',
                 'fuse_remove_signal_handlers', 'fuse_session_destroy',
                 'fuse_unmount', 'fuse_req_ctx', 'fuse_lowlevel_ops',
                 'fuse_session_loop', 'ENOATTR', 'ENOTSUP',
                 'fuse_version', 'fuse_daemonize' ]

# Import ctypeslib
basedir = os.path.abspath(os.path.dirname(sys.argv[0]))
sys.path.insert(0, os.path.join(basedir, 'src', 'ctypeslib.zip'))
from ctypeslib import h2xml, xml2py
from ctypeslib.codegen import codegenerator as ctypeslib

def create_fuse_api():
    '''Create ctypes API to local FUSE headers'''
 
    print('Creating ctypes API from local fuse headers...')

    cflags = get_cflags()
    print('Using cflags: %s' % ' '.join(cflags))  
    
    fuse_path = get_library_path('libfuse.so')
    print('Found fuse library in %s' % fuse_path)
    
    # Create temporary XML file
    tmp_fh = tempfile.NamedTemporaryFile()
    tmp_name = tmp_fh.name
    
    print('Calling h2xml...')
    sys.argv = [ 'h2xml.py', '-o', tmp_name, '-c', '-q', '-I', os.path.join(basedir, 'src'),
                'fuse_ctypes.h' ]
    sys.argv += cflags
    sys.argc = len(sys.argv)
    ctypeslib.ASSUME_STRINGS = False
    ctypeslib.CDLL_SET_ERRNO = False
    ctypeslib.PREFIX = ('# Code autogenerated by ctypeslib. Any changes will be lost!\n\n'
                        '#pylint: disable-all\n'
                        '#@PydevCodeAnalysisIgnore\n\n')
    h2xml.main()
    
    print('Calling xml2py...')
    api_file = os.path.join(basedir, 'src', 'llfuse', 'ctypes_api.py')
    sys.argv = [ 'xml2py.py', tmp_name, '-o', api_file, '-l', fuse_path ]
    for el in export_regex:
        sys.argv.append('-r')
        sys.argv.append(el)
    for el in export_symbols:
        sys.argv.append('-s')
        sys.argv.append(el)
    sys.argc = len(sys.argv)
    xml2py.main()
    
    # Delete temporary XML file
    tmp_fh.close()
  
    print('Code generation complete.')    

def create_libc_api():
    '''Create ctypes API to local libc'''
    
    print('Creating ctypes API from local libc headers...')

    libc_path = b'/lib/libc.so.6'
    if not os.path.exists(libc_path):
        print('Could not load libc (expected at %s)' % libc_path, file=sys.stderr)
        sys.exit(1)
    
    # Create temporary XML file
    tmp_fh = tempfile.NamedTemporaryFile()
    tmp_name = tmp_fh.name
    
    print('Calling h2xml...')
    sys.argv = [ 'h2xml.py', '-o', tmp_name, '-c', '-q', '-I', os.path.join(basedir, 'src'),
                'libc_ctypes.h' ]
    sys.argc = len(sys.argv)
    ctypeslib.ASSUME_STRINGS = True
    ctypeslib.CDLL_SET_ERRNO = True
    ctypeslib.PREFIX = ('# Code autogenerated by ctypeslib. Any changes will be lost!\n\n'
                        '#pylint: disable-all\n'
                        '#@PydevCodeAnalysisIgnore\n\n')
    h2xml.main()
    
    print('Calling xml2py...')
    api_file = os.path.join(basedir, 'src', 's3ql', 'libc_api.py')
    sys.argv = [ 'xml2py.py', tmp_name, '-o', api_file, '-l', libc_path,
                '-s', 'getxattr', '-s', 'setxattr' ]
    sys.argc = len(sys.argv)
    xml2py.main()
    
    # Delete temporary XML file
    tmp_fh.close()
  
    print('Code generation complete.')    
    

def get_cflags():
    '''Get cflags required to compile with fuse library''' 
    
    proc = subprocess.Popen(['pkg-config', 'fuse', '--cflags'], stdout=subprocess.PIPE)
    cflags = proc.stdout.readline().rstrip()
    proc.stdout.close()
    if proc.wait() != 0:
        sys.stderr.write('Failed to execute pkg-config. Exit code: %d.\n' 
                         % proc.returncode)
        sys.stderr.write('Check that the FUSE development package been installed properly.\n')
        sys.exit(1)
    return cflags.split()

  

def get_library_path(lib):
    '''Find location of libfuse.so'''
    
    proc = subprocess.Popen(['/sbin/ldconfig', '-p'], stdout=subprocess.PIPE)
    shared_library_path = None
    for line in proc.stdout:
        res = re.match('^\\s*%s\\.[0-9]+ \\(libc6\\) => (.+)$' % lib, line)
        if res is not None:
            shared_library_path = res.group(1)
            # Slurp rest of output
            for _ in proc.stdout:
                pass
            
    proc.stdout.close()
    if proc.wait() != 0:
        sys.stderr.write('Failed to execute /sbin/ldconfig. Exit code: %d.\n' 
                         % proc.returncode)
        sys.exit(1)
    
    if shared_library_path is None:
        sys.stderr.write('Failed to locate library %s.\n' % lib)
        sys.exit(1)
        
    return shared_library_path

if __name__ == '__main__':
    create_libc_api()
    create_fuse_api()
