#!/usr/bin/env python

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
                 'fuse_session_loop' ]

# Import ctypeslib
basedir = os.path.abspath(os.path.dirname(sys.argv[0]))
sys.path.insert(0, os.path.join(basedir, 'src', 'ctypeslib.zip'))
from ctypeslib import h2xml, xml2py

def main():
    '''Create ctypes API to local FUSE headers'''
    
    print 'Creating ctypes API from local fuse headers...'

    cflags = get_cflags()
    print 'Using cflags: %s' % ' '.join(cflags)  
    
    shared_library_path = get_library_path()
    print 'Found fuse library in %s' % shared_library_path
    
    # Create temporary XML file
    tmp_fh = tempfile.NamedTemporaryFile()
    tmp_name = tmp_fh.name
    
    print 'Calling h2xml...'
    #src/h2xml.py -o fuse_api.xml -I /usr/include/fuse -I src fuse_ctypes.h -D _FILE_OFFSET_BITS=64 -c -q
    sys.argv = [ 'h2xml.py', '-o', tmp_name, '-c', '-q', '-I', os.path.join(basedir, 'src'),
                'fuse_ctypes.h' ]
    sys.argv += cflags
    sys.argc = len(sys.argv)
    h2xml.main()
    
    print 'Calling xml2py...'
    api_file = os.path.join(basedir, 'src', 'llfuse', 'ctypes_api.py')
    #src/xml2py.py fuse_api.xml -r 'FUSE_SET_.*' -r 'XATTR_.*'
    sys.argv = [ 'xml2py.py', tmp_name, '-o', api_file, '-l', shared_library_path ]
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
    
    # Add library location and comments
    tmp = tempfile.TemporaryFile()
    code = open(api_file, "r+")
    for line in code:
        tmp.write(line)
    tmp.seek(0)
    code.seek(0)
    code.truncate()
    code.write('# Code autogenerated by ctypeslib. Any changes will be lost!\n\n')
    code.write('#pylint: disable-all\n')
    code.write('#@PydevCodeAnalysisIgnore\n\n')
    for line in tmp:
        # Evil hack
        if line == 'STRING = c_char_p\n':
            line = 'STRING = POINTER(c_char)\n'
        code.write(line)
    tmp.close()
    #code.write('library_path = %r\n' % shared_library_path)
    #code.write('__all__.append("library_path")\n')
    code.close()
  
    print 'Code generation complete.'    


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

  

def get_library_path():
    '''Find location of libfuse.so'''
    
    proc = subprocess.Popen(['/sbin/ldconfig', '-p'], stdout=subprocess.PIPE)
    shared_library_path = None
    for line in proc.stdout:
        res = re.match('^\\s*libfuse\\.so\\.[0-9]+ \\(libc6\\) => (.+)$', line)
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
        sys.stderr.write('Failed to locate fuse library libfuse.so.\n')
        sys.exit(1)
        
    return shared_library_path

if __name__ == '__main__':
    main()
