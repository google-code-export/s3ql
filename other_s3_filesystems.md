There are quite a number of S3 file systems around. The following table attempts to give an overview. Obviously, it is biased in favor of S3QL because it mainly lists the reasons why the author chose to write a new file system instead of using one of the existing ones.

Please don't hesitate to submit any corrections or additions, I hope that this table will become less biased over time.

Some more S3 file systems are also listed under [Related Projects](related_projects.md).

There are basically three different types of S3 file systems:

  * **Block Based** file systems expose S3 as a single block device which can then be formatted with an ordinary file system. These file systems are conceptually simple, but the performance is very difficult to get right because they work at a very low level.

  * **1:1** file systems save each file in a single S3 object. This has the advantage that the files can be accessed with other S3 tools as well. The disadvantage is that only a very basic functionality can be implemented.

  * **Native** file systems provide the complete set of unix features. They operate at a very high level and can be tailored exactly to the requirements. However, this also makes them very complex and it is very difficult to retrieve the stored data with any other S3 tool.

<p>
<table cellpadding='1' border='1' align='left' cellspacing='2' width='100%'>
<blockquote><tr>
<blockquote><th></th>
<th><a href='http://code.google.com/p/s3ql'>S3QL</a></th>
<th><a href='http://www.persistentfs.com/'>PersistentFS</a></th>
<th><a href='http://code.google.com/p/s3fs/'>S3FS</a></th>
<th><a href='http://github.com/russross/s3fslite'>S3FSLite</a></th>
<th><a href='http://www.subcloud.com/'>SubCloud</a></th>
<th><a href='http://s3backer.googlecode.com/'>S3Backer</a></th>
<th><a href='http://www.elasticdrive.com/'>ElasticDrive</a></th>
</blockquote></tr>
<tr>
<blockquote><th>Type</th>
<td>Native</td>
<td>Native</td>
<td>1:1</td>
<td>1:1</td>
<td>1:1</td>
<td>Block Based</td>
<td>Block Based</td>
</blockquote></tr>
<tr>
<blockquote><th>File Size Limit</th>
<td>unlimited</td>
<td>?</td>
<td>5 GB</td>
<td>5 GB</td>
<td>5 GB</td>
<td>unlimited</td>
<td>unlimited</td>
</blockquote></tr>
<tr>
<blockquote><th>File System Size</th>
<td>dynamic</td>
<td>?</td>
<td>dynamic</td>
<td>dynamic</td>
<td>dynamic</td>
<td>fixed</td>
<td>fixed</td>
</blockquote></tr>
<tr>
<blockquote><th>License</th>
<td>Open Source</td>
<td>Commercial</td>
<td>Open Source</td>
<td>Open Source</td>
<td>Commercial</td>
<td>Open Source</td>
<td>Commercial</td>
</blockquote></tr>
<tr>
<blockquote><th>Compression</th>
<td>Yes</td>
<td>No</td>
<td>No</td>
<td>No</td>
<td>Yes</td>
<td>Yes</td>
<td>?</td>
</blockquote></tr>
<tr>
<blockquote><th>Encryption</th>
<td>Yes</td>
<td>?</td>
<td>No</td>
<td>No</td>
<td>Yes</td>
<td>Via dm-crypt</td>
<td>Via dm-crypt</td>
</blockquote></tr>
<tr>
<blockquote><th>Snapshots /<br />Copy-On-Write</th>
<td>Yes</td>
<td>No</td>
<td>No</td>
<td>No</td>
<td>No</td>
<td>Via LVM</td>
<td>Via LVM</td>
</blockquote></tr>
<tr>
<blockquote><th>Data De-Duplication</th>
<td>Yes</td>
<td>No</td>
<td>No</td>
<td>No</td>
<td>No</td>
<td>No</td>
<td>No</td>
</blockquote></tr>
<tr>
<blockquote><th>Unix Attributes</th>
<td>Yes</td>
<td>?</td>
<td>?</td>
<td>?</td>
<td>?</td>
<td>Yes</td>
<td>Yes</td>
</blockquote></tr>
<tr>
<blockquote><th>Hardlink Support</th>
<td>Yes</td>
<td>?</td>
<td>No</td>
<td>No</td>
<td>No</td>
<td>Yes</td>
<td>Yes</td>
</blockquote></tr>
<tr>
<blockquote><th>Symlink Support</th>
<td>Yes</td>
<td>?</td>
<td>No</td>
<td>No</td>
<td>No</td>
<td>Yes</td>
<td>Yes</td>
</blockquote></tr>
<tr>
<blockquote><th>Directory<br /> Rename Support</th>
<td>Yes</td>
<td>Yes</td>
<td>No</td>
<td>Partial</td>
<td>Partial</td>
<td>Yes</td>
<td>Yes</td>
</blockquote></tr>
<tr>
<blockquote><th>Block size</th>
<td>configurable</td>
<td>?</td>
<td>file</td>
<td>file</td>
<td>file</td>
<td>configurable</td>
<td>configurable</td>
</blockquote></tr>
<tr>
<blockquote><th>Multiple Mounts</th>
<td>No</td>
<td>Yes</td>
<td>No</td>
<td>No</td>
<td>Yes</td>
<td>No</td>
<td>No</td>
</blockquote></tr>
<tr>
<blockquote><th>Eventual<br /> consistency handling</th>
<td>Yes</td>
<td>?</td>
<td>Yes</td>
<td>?</td>
<td>?</td>
<td>?</td>
<td>?</td>
</blockquote></tr>
</table></blockquote>

<h3>Notes</h3>

<ul><li><b>directory rename support: partial</b> means that renaming a directory implies that all contained files and directories need to be copied, so renaming a directory may take a really long time.</li></ul>

<ul><li><b>block size: file</b> means that files can only be transferred in one piece, i.e. changing a few bytes in a 1 GB file means that the whole file has to be uploaded again.</li></ul>

<ul><li><b>Eventual consistency handling</b> refers to the fact that after an object has been uploaded to Amazon S3, it is possible that downloading the object will still return the supposedly overriden old data. Robust file systems need to be able to handle this properly.</li></ul>

<ul><li><b>Multiple Mounts</b> indicates if the same file system can be mounted on several computers at the same time, like e.g. NFS or CIFS.