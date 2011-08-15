'''
s3s.py - this file is part of S3QL (http://s3ql.googlecode.com)

Copyright (C) Nikolaus Rath <Nikolaus@rath.org>

This program can be distributed under the terms of the GNU GPLv3.
'''

from __future__ import division, print_function, absolute_import

from . import s3 
import httplib

class Bucket(s3.Bucket):
    """A bucket stored in Amazon S3
    
    This class uses secure (SSL) connections to connect to S3.
    """
            
    def _get_conn(self, use_ssl):
        '''Return connection to server'''
        
        return httplib.HTTPSConnection('%s.s3.amazonaws.com' % self.bucket_name)        