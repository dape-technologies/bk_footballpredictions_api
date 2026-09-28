
import pymysql


# Django's MySQL backend imports MySQLdb. PyMySQL provides that interface
# without requiring a C compiler, which is unavailable on cPanel hosting.
pymysql.install_as_MySQLdb()
