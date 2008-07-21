#!/usr/bin/env python
#
#    Copyright (C) 2008  Nikolaus Rath <Nikolaus@rath.org>
#
#    This program can be distributed under the terms of the GNU LGPL.
#

import os
import sys
import apsw
import fuse
import traceback
from errno    import *
from stat     import *
from fuse     import Fuse, Direntry
from time     import time
from optparse import OptionParser
from getpass  import getpass
from boto.s3.connection \
              import S3Connection
from string   import Template
from itertools import chain
import functools

# Check fuse version
if not hasattr(fuse, '__version__'):
    raise RuntimeError, \
        "your fuse-py doesn't know of fuse.__version__, probably it's too old."
fuse.fuse_python_api = (0, 2)
fuse.feature_assert('stateful_files', 'has_init')

def fuse_request_handler(fn):
    """Wraps the calls to FUSE request handlers

    If a FUSE request handler throws an exception almost all
    information about the exception is lost, since only the
    errno value can be reported to the caller.

    For this reason, the fuse_request_handler is supposed to
    be used as a decorator for all request handling functions.
    It wraps the handler function and logs all exceptions.
    If the exception specifies so, it also marks the filesystem
    as needing fsck.
    """

    @functools.wraps(fn)
    def wrapped(self, *a, **kw):
        if hasattr(self, "fs"):
            fs = self.fs
            pathinfo = " [path=%s]" % self.path
        else:
            fs = self
            pathinfo = ""

        fname = fn.__name__.rstrip("_i")

        # Print request name and parameters
        if fname == "write": # Special case: very large parameters
            a[0] = buffer(a[0])

        debug("* Received FUSE request %s(%s)%s" %
              (fname, ", ".join(map(repr, chain(a, kw.values()))),
               pathinfo))

        try:
            if fname == "__init__": # Constructors don't return values
                fn(self, *a, **kw)
            else:
                return fn(self, *a, **kw)
        except s3qlException, e:
            error([str(e), "\n",
                   e.fatal and "fs has probably been damaged, run s3fsck as soon as possible\n" or "\n",
                   "path: %s, inode: %s, s3key: %s" % (e.path, e.inode, e.s3key) ]
                  + traceback.format_tb(sys.exc_info()[2]))
            if e.fatal:
                fs.mark_damaged()
            raise

        except Exception, e:
            error(["internal fs error:\n", str(e), "\n",
                   "please report this as a bug!\n",
                   "fs has probably been damaged, run s3fsck as soon as possible\n"]
                  + traceback.format_tb(sys.exc_info()[2]))
            fs.mark_damaged()
            raise
    return wrapped



class s3qlException(IOError):
    """Class for exceptions generated by s3qlfs

    Inherits from IOError so that FUSE detects and reports the correct
    errno.

    Attributes
    ----------

    :errno:  Errno to return to FUSE
    :desc:   Detailed text error message")
    :fatal:  Should be set if the error left the filesystem in an inconsistent state

    :path:   The path for which the error occured (if applicable)
    :inode:  The inode for which the error occured (if applicable)
    :s3key:  The s3 object key for which the error occured (if applicable)
    """

    def __init__(self, desc, errno=EIO, path=None, inode=None,
                 s3key = None, fatal=False):
        self.errno = errno
        self.desc = desc
        self.path = path
        self.inode = inode
        self.s3key = s3key
        self.fatal = fatal

    def __str__(self):
        return self.desc




class s3qlfs(Fuse):
    """ FUSE filesystem that stores its data on Amazon S3
    """

    def __init__(self, bucketname, mountpoint, awskey=None,
                 awspass=None, debug_on=False, fuse_options=None):
        Fuse.__init__(self)

        # We mess around to pass ourselves to the file class
        class file_class (s3qlFile):
            def __init__(self2, *a, **kw):
                s3qlFile.__init__(self2, self, *a, **kw)
        self.file_class = file_class

        self.fuse_options = fuse_options
        self.bucketname = bucketname
        self.awskey = awskey
        self.awspass = awspass
        self.mountpoint = mountpoint
        self.dbdir = os.environ["HOME"].rstrip("/") + "/.s3qlfs/"
        self.dbfile = self.dbdir + bucketname + ".db"
        self.cachedir = self.dbdir + bucketname + "-cache/"

    # This function is also called internally, so we define
    # an unwrapped version
    def getattr_i(self, path):
        """Handles FUSE getattr() requests
        """

        stat = fuse.Stat()
        try:
            res = self.cursor.execute("SELECT mode, refcount, uid, gid, size, inode, rdev, "
                                      "atime, mtime, ctime FROM contents_ext WHERE name=? ",
                                      (buffer(path),))
            (stat.st_mode,
             stat.st_nlink,
             stat.st_uid,
             stat.st_gid,
             stat.st_size,
             stat.st_ino,
             stat.st_rdev,
             stat.st_atime,
             stat.st_mtime,
             stat.st_ctime) = res.next()
        except StopIteration:
            return -ENOENT

        # FIXME: Preferred blocksize for doing IO
        stat.st_blksize = 512 * 1024

        if S_ISREG(stat.st_mode):
            # determine number of blocks for files
            stat.st_blocks = int(stat.st_size/512)
        else:
            # For special nodes, return arbitrary values
            stat.st_size = 512
            stat.st_blocks = 1

        # Not applicable and/or overwritten anyway
        stat.st_dev = 0

        # Device ID = 0 unless we have a device node
        if not S_ISCHR(stat.st_mode) and not S_ISBLK(stat.st_mode):
            stat.st_rdev = 0

        # Make integers
        stat.st_mtime = int(stat.st_mtime)
        stat.st_atime = int(stat.st_atime)
        stat.st_ctime = int(stat.st_ctime)

        return stat
    getattr = fuse_request_handler(getattr_i)

    @fuse_request_handler
    def readlink(self, path):
        """Handles FUSE readlink() requests.
        """

        (target,inode) = self.cursor.execute\
            ("SELECT target,inode FROM contents_ext WHERE name=?", (buffer(path),)).next()

        self.update_atime(inode=inode)

        return str(target)

    @fuse_request_handler
    def readdir(self, path, offset):
        """Handles FUSE readdir() requests
        """

        stat = self.getattr_i(path)

        self.update_atime(inode=stat.st_ino)

        # Current directory
        yield Direntry(".", ino=stat.st_ino, type=S_IFDIR)

        # Parent directory
        if path == "/":
            yield Direntry("..", ino=stat.st_ino, type=S_IFDIR)
            lookup = "/?*"
            strip = 1
        else:
            parent = path[:path.rindex("/")]
            if parent == "":
                parent = "/"
            yield Direntry("..", ino=self.getattr_i(parent).st_ino,
                           type=S_IFDIR)
            lookup = path + "/?*"
            strip = len(path)+1

        # Actual contents
        res = self.cursor.execute("SELECT name,inode,mode FROM contents_ext WHERE name GLOB ?",
                                  (buffer(lookup),))
        for (name,inode,mode) in res:
            yield Direntry(str(name)[strip:], ino=inode, type=S_IFMT(mode))


    def update_atime(self, inode=None, path=None):
        """Updates the mtime of the specified object.

        Only one of the `inode` and `path` parameters must be given
        to identify the object. The objects atime will be set to the
        current time.
        """

        if (inode and path) or (not inode and not path):
            raise s3qlException("update_mtime must be called with exactly one parameter.")

        if path:
            (inode,) = self.cursor.execute("SELECT inode FROM contents WHERE name=?",
                                           (buffer(path),)).next()

        self.cursor.execute("UPDATE inodes SET atime=? WHERE id=?", (time(), inode))




    def update_parent_mtime(self, path):
        """Updates the mtime of the parent directory of the specified object.

        The mtime of the directory containing the specified object will be set
        to the current time.
        """

        parent = path[:path.rindex("/")]
        if parent == "":
            parent = "/"

        self.cursor.execute("UPDATE inodes SET mtime=? WHERE id=?",
                            (time(), self.getattr_i(parent).st_ino))


    def lock_inode(self, id):
        """ locks the specified inode

        If the entry is already locked, the function waits until
        the lock is released.
        """
        # The python implementation is single threaded
        pass

    def unlock_inode(self, id):
        """ unlocks the specified inode

        This function must only be called if we are actually holding
        a lock on the given entry.
        """
        # The python implementation is single threaded
        pass


    @fuse_request_handler
    def unlink(self, path):
        """Handles FUSE unlink(( requests.

        Implementation depends on the ``hard_remove`` FUSE option
        not being used.
        """

        (inode,refcount) = self.cursor.execute \
            ("SELECT inode,refcount FROM contents_ext WHERE name=?", (buffer(path),)).next()

        self.cursor.execute("DELETE FROM contents WHERE name=?", (buffer(path),))

        # No more links, remove datablocks
        if refcount == 1:
            res = self.cursor.execute("SELECT s3key FROM s3_objects WHERE inode=?", (inode,))
            for (id,) in res:
                bucket.delete_key(id)

            self.cursor.execute("DELETE FROM s3_objects WHERE inode=?", (inode,))
            self.cursor.execute("DELETE FROM inodes WHERE id=?", (inode,))
        else:
            self.cursor.execute("UPDATE inodes SET ctime=? WHERE id=?", (time(), inode))

        self.update_parent_mtime(path)


    def mark_damaged(self):
        """Marks the filesystem as being damaged and needing fsck.
        """

        self.cursor.execute("UPDATE parameters SET needs_fsck=?", (True,))


    @fuse_request_handler
    def rmdir(self, path):
        """Handles FUSE rmdir() requests.
        """

        inode = self.getattr_i(path).st_ino

        # Check if directory is empty
        try:
            self.cursor.execute \
            ("SELECT * FROM contents WHERE name GLOB ? LIMIT 1", (buffer(path + "/*"),)).next()
        except StopIteration:
            pass # That's what we want
        else:
            return -EINVAL

        # Delete
        self.cursor.execute("BEGIN TRANSACTION")
        try:
            self.cursor.execute("DELETE FROM contents WHERE name=?", (buffer(path),))
            self.cursor.execute("DELETE FROM inodes WHERE id=?", (inode,))
            self.update_parent_mtime(path)
        except:
            self.cursor.execute("ROLLBACK")
            raise
        else:
            self.cursor.execute("COMMIT")


    @fuse_request_handler
    def symlink(self, target, name):
        """Handles FUSE symlink() requests.
        """

        con = self.GetContext()
        self.cursor.execute("BEGIN TRANSACTION")
        try:
            self.cursor.execute("INSERT INTO inodes (mode,uid,gid,target,mtime,atime,ctime) "
                                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                                (S_IFLNK, con["uid"], con["gid"], buffer(target), time(), time(), time()))
            self.cursor.execute("INSERT INTO contents(name, inode) VALUES(?, ?)",
                                (buffer(name), self.conn.last_insert_rowid()))
            self.update_parent_mtime(name)
        except:
            self.cursor.execute("ROLLBACK")
            raise
        else:
            self.cursor.execute("COMMIT")

    @fuse_request_handler
    def rename(self, path, path1):
        """Handles FUSE rename() requests.
        """

        self.cursor.execute("UPDATE contents SET name=? WHERE name=?", (buffer(path1), buffer(path)))
        self.update_parent_mtime(path)
        self.update_parent_mtime(path1)


    @fuse_request_handler
    def link(self, path, path1):
        """Handles FUSE link() requests.
        """

        stat = self.getattr_i(path)

        self.cursor.execute("INSERT INTO contents (name,inode) VALUES(?,?)"
                            (buffer(path1), stat.st_ino))
        self.cursor.execute("UPDATE inodes SET ctime=? WHERE id=?", (time(), stat.st_ino))
        self.update_parent_mtime(path1)


    @fuse_request_handler
    def chmod(self, path, mode):
        """Handles FUSE chmod() requests.
        """

        self.cursor.execute("UPDATE inodes SET mode=?,ctime=? WHERE id=(SELECT inode "
                            "FROM contents WHERE name=?)", (mode, time(), buffer(path)))

    @fuse_request_handler
    def chown(self, path, user, group):
        """Handles FUSE chown() requests.
        """

        self.cursor.execute("UPDATE inodes SET uid=?, gid=?, ctime=? WHERE id=(SELECT inode "
                            "FROM contents WHERE name=?)", (user, group, time(), buffer(path)))

    # This function is also called internally, so we define
    # an unwrapped version
    def mknod_i(self, path, mode, dev=None):
        """Handles FUSE mknod() requests.
        """

        con = self.GetContext()
        self.cursor.execute("BEGIN TRANSACTION")
        try:
            self.cursor.execute("INSERT INTO inodes (mtime,ctime,atime,uid, gid, mode, size, rdev) "
                                "VALUES(?, ?, ?, ?, ?, ?, 0, ?)",
                                (time(), time(), time(), con["uid"], con["gid"], mode, dev))
            self.cursor.execute("INSERT INTO contents(name, inode) VALUES(?, ?)",
                                (buffer(path), self.conn.last_insert_rowid()))
            self.update_parent_mtime(path)
        except:
            self.cursor.execute("ROLLBACK")
            raise
        else:
            self.cursor.execute("COMMIT")
    mknod = fuse_request_handler(mknod_i)


    @fuse_request_handler
    def mkdir(self, path, mode):
        """Handles FUSE mkdir() requests.
        """

        mode |= S_IFDIR # Set type to directory
        con = self.GetContext()
        self.cursor.execute("BEGIN TRANSACTION")
        try:
            self.cursor.execute("INSERT INTO inodes (mtime,atime,ctime,uid, gid, mode) "
                                "VALUES(?, ?, ?, ?, ?, ?)",
                                (time(), time(), time(), con["uid"], con["gid"], mode))
            self.cursor.execute("INSERT INTO contents(name, inode) VALUES(?, ?)",
                                (buffer(path), self.conn.last_insert_rowid()))
            self.update_parent_mtime(path)
        except:
            self.cursor.execute("ROLLBACK")
            raise
        else:
            self.cursor.execute("COMMIT")

    @fuse_request_handler
    def utime(self, path, times):
        """Handles FUSE utime() requests.
        """

        (atime, mtime) = times
        self.cursor.execute("UPDATE inodes SET atime=?,mtime=?,ctime=? WHERE id=(SELECT inode "
                            "FROM contents WHERE name=?)", (atime, mtime, time(), buffer(path)))


    @fuse_request_handler
    def statfs(self):
        """Handles FUSE statfs() requests.
        """

        stat = fuse.StatVfs()

        # FIMXME: Blocksize, basically random
        stat.f_bsize = 1024*1024*512 # 512 KB
        stat.f_frsize = stat.f_bsize

        # Get number of blocks & inodes blocks
        (blocks,) = self.cursor.execute("SELECT COUNT(s3key) FROM s3_objects").next()
        (inodes,) = self.cursor.execute("SELECT COUNT(id) FROM inodes").next()

        # Since S3 is unlimited, always return a half-full filesystem
        stat.f_blocks = 2 * blocks
        stat.f_bfree = blocks
        stat.f_bavail = blocks
        stat.f_files = 2 * inodes
        stat.f_ffree = inodes

        return stat


    @fuse_request_handler
    def truncate(self, bpath, len):
        """Handles FUSE truncate() requests.
        """

        file = self.file_class()
        file.opencreate(bpath, os.O_WRONLY)
        file.ftruncate_i(len)
        file.release_i()

    def main(self):
        """Starts the main loop handling FUSE requests.

        This implementation only runs single-threaded.
        """

        # Preferences
        if not os.path.exists(self.dbdir):
            os.mkdir(self.dbdir)


        # Connect to S3
        debug("Connecting to S3...")
        self.bucket = S3Connection(self.awskey, self.awspass).\
            get_bucket(self.bucketname)

        # Check consistency
        debug("Checking consistency...")
        key = self.bucket.new_key("dirty")
        if key.get_contents_as_string() != "no":
            print >> sys.stderr, \
                "Metadata is dirty! Either some changes have not yet propagated\n" \
                "through S3 or the filesystem has not been umounted cleanly. In\n" \
                "the later case you should run s3fsck on the system where the\n" \
                "filesystem has been mounted most recently!\n"
            sys.exit(1)

        # Download metadata
        debug("Downloading metadata...")
        if os.path.exists(self.dbfile):
            print >> sys.stderr, \
                "Local metadata file already exists! Either you are trying to\n" \
                "to mount a filesystem that is already mounted, or the filesystem\n" \
                "has not been umounted cleanly. In the later case you should run\n" \
                "s3fsck.\n"
            sys.exit(1)
        os.mknod(self.dbfile, 0600 | S_IFREG)
        key = self.bucket.new_key("metadata")
        key.get_contents_to_filename(self.dbfile)

        # Init cache
        if os.path.exists(self.cachedir):
            print >> sys.stderr, \
                "Local cache files already exists! Either you are trying to\n" \
                "to mount a filesystem that is already mounted, or the filesystem\n" \
                "has not been umounted cleanly. In the later case you should run\n" \
                "s3fsck.\n"
            sys.exit(1)
        os.mkdir(self.cachedir, 0700)

        # Connect to db
        debug("Connecting to db...")
        self.conn = apsw.Connection(self.dbfile)
        self.conn.setbusytimeout(5000)
        self.cursor = self.conn.cursor()

        # Get blocksize
        debug("Reading fs parameters...")
        try:
            (self.blocksize,) = self.cursor.execute("SELECT blocksize FROM parameters").next()
        except StopIteration:
            print >> sys.stderr, "Filesystem damaged, run s3fsk!\n"
            sys.exit(1)

        # Check that the fs itself is clean
        dirty = False
        try:
            (dirty,) = self.cursor.execute("SELECT needs_fsck FROM parameters").next()
        except StopIteration:
            dirty = True
        if dirty:
            print >> sys.stderr, "Filesystem damaged, run s3fsk!\n"
            #os.unlink(self.dbfile)
            #os.rmdir(self.cachedir)
            #sys.exit(1)

        # Update mount count
        self.cursor.execute("UPDATE parameters SET mountcnt = mountcnt + 1")

        # Start main event loop
        debug("Starting main event loop...")
        self.multithreaded = 0
        mountoptions =  [ "direct_io",
                          "default_permissions",
                          "use_ino",
                          "fsname=s3qlfs" ] + self.fuse_options
        Fuse.main(self, [sys.argv[0], "-f", "-o" + ",".join(mountoptions),
                         self.mountpoint])


        # Flush file and datacache
        debug("Flushing cache...")
        res = self.cursor.execute(
            "SELECT s3key, fd, dirty, cachefile FROM s3_objects WHERE fd IS NOT NULL")
        for (s3key, fd, dirty, cachefile) in list(res): # copy list to reuse cursor
            debug("\tCurrent object: " + s3key)
            os.close(fd)
            if dirty:
                error([ "Warning! Object ", s3key, " has not yet been flushed.\n",
                             "Please report this as a bug!\n" ])
                key = self.bucket.new_key(s3key)
                key.set_contents_from_filename(self.cachedir + cachefile)
            self.cursor.execute("UPDATE s3_objects SET dirty=?, cachefile=?, "
                                "etag=?, fd=? WHERE s3key=?",
                                (False, None, key.etag, None, s3key))
            os.unlink(self.cachedir + cachefile)


        # Upload database
        debug("Uploading database..")
        self.cursor.execute("VACUUM")
        self.conn.close()
        key = self.bucket.new_key("metadata")
        key.set_contents_from_filename(self.dbfile)

        key = self.bucket.new_key("dirty")
        key.set_contents_from_string("no")

        # Remove database
        debug("Cleaning up...")
        os.unlink(self.dbfile)
        os.rmdir(self.cachedir)



class s3qlFile(object):
    """Class representing open files in s3qlfs.

    Attributes
    ----------

    :cursor: cursor for metadata db
    :conn:   connection to metadata db
    :fs:     s3qlfs instance belonging to this file
    """

    def __init__(self, fs, *a, **kw):
        """Handles FUSE open() and create() requests.
        """

        self.fs = fs
        self.cursor = self.fs.cursor
        self.conn = self.fs.conn

        # FIXME: Apparenty required, even though passed as parameter to fuse
        self.direct_io = True
        self.keep_cache = None

        if len(a) == 0 and len(kw) == 0: # internal call
            return
        else:
            self.opencreate(bpath, flags, mode)

    def opencreate(self, bpath, flags, mode=None)
        self.path = bpath
        self.flags = flags

        # Create if not existing
        if mode:
            self.fs.mknod_i(bpath, mode)

        self.inode = self.fs.getattr_i(bpath).st_ino


    @fuse_request_handler
    def read(self, length, offset):
        """Handles FUSE read() requests.

        May return less than `length` bytes, to the ``direct_io`` FUSE
        option has to be enabled.
        """

        (s3key, offset_i, fd) = self.get_s3(offset)

        # If we do not reach the desired position, then
        # we have a hole and return \0
        if os.lseek(fd,offset - offset_i, os.SEEK_SET) != offset - offset_i:
            return "\0" * length

        self.fs.update_atime(inode=self.inode)
        return os.read(fd,length)


    def get_s3(self, offset, create=False, offset_min=0):
        """Returns s3 object containing given file offset.

        Searches for an S3 object with minimum offset `offset_min` and maximum
        offset 'offset'. If the s3 object is not already cached, it is retrieved
        from Amazon and put into the cache.

        If no such object exists and create=True, the object is
        created with offset 'offset_min'.

        The return value is a tuple (s3key, offset, fd) or None if the object
        does not exist.
        """

        # We need to avoid that another thread retrieves or
        # creates the object at the same time
        self.fs.lock_inode(self.inode)

        try:
            # Try to find key
            (s3key, offset_i, fd, etag) = self.cursor.execute(
                "SELECT s3key, offset, fd, etag FROM s3_objects WHERE inode = ? "
                "AND offset <= ? AND offset >= ? ORDER BY offset DESC LIMIT 1",
                (self.inode, offset, offset_min)).next()

            if fd is None:
                # FIXME: Check if there is space available in
                # the cache.
                fd = self.retrieve_s3(s3key, etag)

            self.cursor.execute("UPDATE s3_objects SET atime=? WHERE s3key=?",
                                (time(), s3key))
            return (s3key, offset_i, fd)

        except StopIteration:
            if not create:
                return None
            else:
                # Create object with minimum offset
                offset_i = offset_min
                (s3key, fd) = self.create_s3(offset_i)
                return (s3key, offset_i, fd)
        finally:
            self.fs.unlock_inode(self.inode)


    def retrieve_s3(self, s3key, etag):
        """Retrieves an S3 object from Amazon and stores it in cache.

        Returns fd of the cachefile. No locking is done. If the etag
        doesn't match the retrieved data, the retrieval is repeated
        after a short interval (to cope with S3 propagation delays).
        In case of repeated failure, an error is returned.
        """

        cachefile = s3key[1:].replace("/", "_")
        cachepath = self.fs.cachedir + cachefile
        key = self.fs.bucket.new_key(s3key)
        key.get_contents_to_filename(cachepath)

        # Check etag
        if key.etag != etag:
            debug("etag mismatch, trying to refetch..")
            waited = 0
            waittime = 0.01
            while key.etag != etag and \
                    waited < self.fs.timeout:
                time.sleep(waittime)
                waited += waittime
                waittime *= 1.5
                key = self.bucket.get_key(s3key) # Updates metadata

            # If still not found
            if key.etag != etag:
                raise S3fsException("Object etag doesn't match metadata!",
                                    s3key=s3key, inode=self.inode, path=self.path,
                                    fatal=True)
            else:
                self.fs.error("etag mismatch for "+ s3key + ". Try increasing the cache size... ")
                key.get_contents_to_filename(cachepath)

        fd = os.open(path, os.O_RDWR)
        self.cursor.execute(
            "UPDATE s3_objects SET dirty=?, fd=?, cachefile=? "
            "WHERE s3key=?", (False, fd, cachefile, s3key))

        return fd

    def create_s3(self, offset):
        """Creates an S3 object with given file offset.

        Returns (s3key,fd). No locking is done and no checks are
        performed if the object already exists.
        """

        # Ensure that s3key is ASCII
        s3key = repr(self.path)[1:-1] + "__" + ("%016d" % offset)
        cachefile = s3key[1:].replace("/", "_")
        cachepath = self.fs.cachedir + cachefile
        fd = os.open(cachepath, os.O_RDWR | os.O_CREAT)
        self.cursor.execute(
            "INSERT INTO s3_objects(inode,offset,dirty,fd,s3key,cachefile, atime) "
            "VALUES(?,?,?,?,?,?,?)", (self.inode, offset, True, fd, s3key, cachefile, time()))

        k = self.fs.bucket.new_key(s3key)
        k.set_contents_from_string("dirty")
        return (s3key,fd)


    def write_i(self, buf, offset):
        """Handles FUSE write() requests.

        May write less byets than given in `buf`, to the ``direct_io`` FUSE
        option has to be enabled.
        """

        # Lookup the S3 object into we have to write. The object
        # should start not earlier than the last block boundary
        # before offset, because otherwise we would extend the
        # object beyound the desired blocksize
        (s3key, offset_i, fd) = self.get_s3(
            offset, create = True,
            offset_min = self.fs.blocksize * int(offset/self.fs.blocksize))

        try:
            # Offset of the next s3 object
            offset_f = self.cursor.execute(
                "SELECT s3key FROM s3_objects WHERE inode = ? "
                "AND offset > ? ORDER BY offset ASC LIMIT 1",
                (self.inode, offset)).next()[0]
            # Filesize will not change with this write
            adjsize = False
        except StopIteration:
            # If there is no next s3 object, we also need to
            # update the filesize
            offset_f = None
            adjsize = True

        # We write at most one block
        if offset_f is None or offset_f > offset_i + self.fs.blocksize:
            offset_f = offset_i + self.fs.blocksize
        maxwrite = offset_f - offset_i

        # Determine number of bytes to write and write
        os.lseek(fd, offset - offset_i, os.SEEK_SET)
        if len(buf) > maxwrite:
            writelen = maxwrite
            writelen = os.write(fd, buf[:maxwrite])
        else:
            writelen = os.write(fd,buf)

        # Update filesize if we are working at the last chunk
        if adjsize:
            obj_len = os.lseek(fd, 0, os.SEEK_END)
            self.cursor.execute("UPDATE inodes SET size=?,ctime=? WHERE id=?",
            (offset_i + obj_len, time(), self.inode))

        # Update file mtime
        self.cursor.execute("UPDATE inodes SET mtime=? WHERE id = ?",
                            (time(), self.inode))
        return writelen
    write = fuse_request_handler(write_i)


    def ftruncate_i(self, len):
        """Handles FUSE ftruncate() requests.
        """

        # Delete all truncated s3 objects
        res = self.cursor.execute(
            "SELECT s3key,fd,cachefile FROM s3_objects WHERE "
            "offset >= ? AND inode=?", (len,inode))

        for (s3key, fd, cachefile) in res:
            if fd: # File is in cache
                os.close(fd,0)
                os.unlink(self.fs.cachedir + cachefile)
            self.fs.bucket.delete_key(s3key)
        self.cursor.execute("DELETE FROM s3_objects WHERE "
                            "offset >= ? AND inode=?", (len,inode))

        # Get last object before truncation
        (s3key, offset_i, fd) = self.get_s3(offset)
        cursize = offset_i + os.lseek(fd, 0, os.SEEK_END)

        # If we are actually extending the file, we just write a
        # 0-byte at the last position
        if len > cursize:
            self.write_i("\0", len-1)

        # Otherwise we truncate the file and update
        # the file size
        else:
            os.ftruncate(fd, len - offset_i)
            self.cursor.execute("UPDATE inodes SET size=? WHERE id=?",
                                (len, self.inode))

        # Update file's mtime
        self.cursor.execute("UPDATE inodes SET mtime=?,ctime=? WHERE id = ?",
                            (time(), time(), self.inode))
    ftruncate = fuse_request_handler(ftruncate_i)

    def release_i(self, flags):
        """Handles FUSE release() requests.
        """
        pass
    release = fuse_request_handler(release_i)


    def fsync_i(self, fdatasync):
        """Handles FUSE fsync() requests.
        """

        # Metadata is always synced automatically, so we ignore
        # fdatasync
        res = self.cursor.execute(
            "SELECT s3key, fd, cachefile FROM s3_objects WHERE "
            "dirty=? AND inode=?", (True, self.inode))
        for (s3key, fd, cachefile) in list(res): # Duplicate res, because we need the cursor
            # We need to mark as clean *before* we write the changes,
            # because otherwise we would loose changes that occured
            # while we wrote to S3
            self.cursor.execute("UPDATE s3_objects SET dirty=? WHERE s3key=?",
                                (False, s3key))
            os.fsync(fd)
            key = self.fs.bucket.new_key(s3key)
            key.set_contents_from_filename(self.fs.cachedir + cachefile)
            self.cursor.execute("UPDATE s3_objects SET etag=? WHERE s3key=?",
                                (key.etag, s3key))
    fsync = fuse_request_handler(fsync_i)


    # Called for close() calls. Here we sync the data, so that we
    # can still return write errors.
    @fuse_request_handler
    def flush(self):
        """Handles FUSE flush() requests.
        """
        return self.fsync_i(False)

    @fuse_request_handler
    def fgetattr(self):
        """Handles FUSE fgetattr() requests.
        """
        return self.fs.getattr_i(self.path)



def debug(arg):
    """ Log message if debug output has been activated
    """

    if not debug_enabled:
        return

    if type(arg) != type([]):
        arg = [arg, "\n"]

    print "".join(arg),


def error(arg):
    """ Log error message
    """

    if type(arg) != type([]):
        arg = [arg, "\n"]

    print >>sys.stderr, "".join(arg),



# If called as program
if __name__ == '__main__':
    parser = OptionParser(
        usage="%prog  [options] <bucketname> <mountpoint>\n"
              "       %prog --help",
        description="Mounts an amazon S3 bucket as a filesystem")

    parser.add_option("--awskey", type="string",
                      help="Amazon Webservices access key to use")
    parser.add_option("--debug", action="store_true", default=False,
                      help="Generate debugging output")
    parser.add_option("--s3timeout", type="int", default=50,
                      help="Maximum time to wait for propagation in S3 (default: %default)")
    parser.add_option("--allow_others", action="store_true", default=False,
                      help="Allow others users to access the filesystem")
    parser.add_option("--allow_root", action="store_true", default=False,
                      help="Allow root to access the filesystem")

    (options, pps) = parser.parse_args()

    if False and options.awskey:
        # Read password
        if sys.stdin.isatty():
            pw = getpass("Enter AWS password: ")
        else:
            pw = sys.stdin.readline().rstrip()
    else:
        pw = None
        pw = "wDJSauZ0a67zWOhpnjrvpdGfCiAt+j86IJ1imsTE"

    if not len(pps) == 2:
        parser.error("Wrong number of parameters")

    fuse_opts = []
    if options.allow_others:
        fuse_opts.append("allow_others")
    if options.allow_root:
        fuse_opts.append("allow_root")


    # Global variable
    if options.debug:
        debug_enabled = True
    else:
        debug_enabled = False


    server = s3qlfs(awskey=options.awskey,
                  bucketname=pps[0],
                  awspass=pw,
                  fuse_options=fuse_opts,
                  mountpoint=pps[1])
    server.main()
