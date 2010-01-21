#!/usr/bin/env python
'''
$Id$

Copyright (C) 2008-2009 Nikolaus Rath <Nikolaus@rath.org>

This program can be distributed under the terms of the GNU LGPL.
'''

from __future__ import division, print_function
    
from distutils.core import setup, Command
import distutils.command.build  
from s3ql.common import init_logging
import sys
import os
import tempfile
import subprocess
import re

# These are the definitons that we need 
fuse_export_regex = ['^FUSE_SET_.*', '^XATTR_.*', 'fuse_reply_.*' ]
fuse_export_symbols = ['fuse_mount', 'fuse_lowlevel_new', 'fuse_add_direntry',
                 'fuse_set_signal_handlers', 'fuse_session_add_chan',
                 'fuse_session_loop_mt', 'fuse_session_remove_chan',
                 'fuse_remove_signal_handlers', 'fuse_session_destroy',
                 'fuse_unmount', 'fuse_req_ctx', 'fuse_lowlevel_ops',
                 'fuse_session_loop', 'ENOATTR', 'ENOTSUP',
                 'fuse_version', 'fuse_daemonize' ]
libc_export_symbols = [ 'setxattr', 'getxattr', 'readdir', 'opendir',
                       'closedir' ]
    
class build_ctypes(Command):
    
    description = "Build ctypes interfaces"
    user_options = []
    boolean_options = []
     
    def initialize_options(self):
         pass
     
    def finalize_options(self):
        pass

    def run(self):
        self.create_fuse_api()
        self.create_libc_api()

    def create_fuse_api(self):
        '''Create ctypes API to local FUSE headers'''
     
         # Import ctypeslib
        basedir = os.path.abspath(os.path.dirname(sys.argv[0]))
        sys.path.insert(0, os.path.join(basedir, 'src', 'ctypeslib.zip'))
        from ctypeslib import h2xml, xml2py
        from ctypeslib.codegen import codegenerator as ctypeslib 
    
        print('Creating ctypes API from local fuse headers...')
    
        cflags = self.get_cflags()
        print('Using cflags: %s' % ' '.join(cflags))  
        
        fuse_path = self.get_library_path('libfuse.so')
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
        for el in fuse_export_regex:
            sys.argv.append('-r')
            sys.argv.append(el)
        for el in fuse_export_symbols:
            sys.argv.append('-s')
            sys.argv.append(el)
        sys.argc = len(sys.argv)
        xml2py.main()
        
        # Delete temporary XML file
        tmp_fh.close()
      
        print('Code generation complete.')    
    
    def create_libc_api(self):
        '''Create ctypes API to local libc'''
    
         # Import ctypeslib
        basedir = os.path.abspath(os.path.dirname(sys.argv[0]))
        sys.path.insert(0, os.path.join(basedir, 'src', 'ctypeslib.zip'))
        from ctypeslib import h2xml, xml2py
        from ctypeslib.codegen import codegenerator as ctypeslib 
            
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
        sys.argv = [ 'xml2py.py', tmp_name, '-o', api_file, '-l', libc_path ]
        for el in libc_export_symbols:
            sys.argv.append('-s')
            sys.argv.append(el)
        sys.argc = len(sys.argv)
        xml2py.main()
        
        # Delete temporary XML file
        tmp_fh.close()
      
        print('Code generation complete.')    
        
    
    def get_cflags(self):
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
    
      
    
    def get_library_path(self, lib):
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

# Add as subcommand of build
distutils.command.build.build.sub_commands.insert(0, ('build_ctypes', None))
   
class run_tests(Command):
    
    description = "Run self-tests"
    user_options = [('debug=', None, 'Activate debugging for specified facilitites '
                                    '(separated by commas)'),
                    ('awskey=', None, 'Specify AWS access key to use, secret key will be asked for'),
                    ('credfile=', None, 'Try to read AWS access key and secret key from specified '
                                       'file (instead of ~/.awssecret)'),
                    ('tests=', None, 'Run only the specified tests (separated by commas)')
                ]                     
    boolean_options = []
     
    def initialize_options(self):
        self.debug = None
        self.awskey = None
        self.credfile = None
        self.tests = None
     
    def finalize_options(self):
        if self.debug:
            self.debug = [ x.strip() for x  in self.debug.split(',') ]
            
        if self.tests:
            self.tests = [ x.strip() for x  in self.tests.split(',') ]
      

    def run(self):
        
        # Enforce correct APSW version
        import apsw
        tmp = apsw.apswversion()
        tmp = tmp[:tmp.index('-')]
        apsw_ver = tuple([ int(x) for x in tmp.split('.') ])
        if apsw_ver < (3, 6, 14):    
            raise StandardError('APSW version too old, must be 3.6.14 or newer!\n')
            
        # Enforce correct SQLite version    
        sqlite_ver = tuple([ int(x) for x in apsw.sqlitelibversion().split('.') ])
        if sqlite_ver < (3, 6, 17):    
            raise StandardError('SQLite version too old, must be 3.6.17 or newer!\n')           

        # Add test modules
        basedir = os.path.abspath(os.path.dirname(sys.argv[0]))
        sys.path = [os.path.join(basedir, 'src'),
                    os.path.join(basedir, 'tests')] + sys.path
     
        import unittest
        import _common
        from s3ql.common import init_logging
        from getpass import getpass
        
        # Init Logging
        if self.debug:
            init_logging(True, False, self.debug)
        else:
            init_logging(True, True)            
        
        # Init AWS
        if self.credfile:
            _common.keyfile = self.credfile
        if self.awskey:
            if sys.stdin.isatty():
                pw = getpass("Enter AWS password: ")
            else:
                pw = sys.stdin.readline().rstrip() 
            _common.aws_credentials_available = True
            _common.aws_credentials = (self.awskey, pw)   
        _common.get_aws_credentials()
        
        # Find and import all tests
        testdir = os.path.join(basedir, 'tests')
        modules_to_test =  [ name[:-3] for name in os.listdir(testdir) 
                            if name.endswith(".py") and name.startswith('t') ]
        modules_to_test.sort()
        #modules_to_test = [ __import__(name) for name in modules_to_test ]
        
        base_module = sys.modules[__name__]
        for name in modules_to_test:
            # Note that __import__ itself does not add the modules to the namespace
            module = __import__(name)
            setattr(base_module, name, module)
        
        if self.tests:
            modules_to_test = self.tests
                
        # Run tests
        runner = unittest.TextTestRunner(verbosity=2)
        tests = unittest.defaultTestLoader.loadTestsFromNames(modules_to_test, module=base_module)
        result = runner.run(tests)
        if not result.wasSuccessful():
            sys.exit(1)      


setup(name='s3ql',
      version='alpha3',
      description='a FUSE filesystem that stores data on Amazon S3',
      author='Nikolaus Rath',
      author_email='Nikolaus@rath.org',
      url='http://code.google.com/p/s3ql/',
      package_dir={'': 'src'},
      packages=['s3ql', 'llfuse'],
      provides=['s3ql'],
      scripts=[ 'bin/fsck.s3ql',
                'bin/mkfs.s3ql',
                'bin/mount.s3ql_local',
                'bin/mount.s3ql' ],
      requires=['apsw', 'boto', 'pycryptopp' ],
      cmdclass={'test': run_tests,
                'build_ctypes': build_ctypes,}
     )
