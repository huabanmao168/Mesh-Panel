"""MeshPanel 版本号。

dev 模式下是 'dev';打包发版时 CI(release.yml) 会用 tag 名覆写这一行。
覆写格式: __version__ = "2.0.1"  (跟 'tag_name'.lstrip('v') 对齐)
"""
__version__ = "dev"
