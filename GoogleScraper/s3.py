# -*- coding: utf-8 -*-

import csv
from io import BytesIO, StringIO
import json

import tinys3


### dict/key with environment!
### 
### 

SCRAPER_TO_LOAD='scraper_to_load'


class S3Table:
    
    def __init__(self, table_obj, scrape_id, env):
        self._table_obj = table_obj
        self._scrape_id = scrape_id
        self._env = env
        ##
        self._tablename = table_obj.__tablename__
        self._table_file = '{0}-{1}.csv'.format(self._tablename, self._scrape_id)
        self._manifest_file = 'l2wr_{0}_{1}_manifest'.format(self.tablename,
                                                             self._scrape_id)
        ##
        self._buffer = StringIO()
        self._writer = csv.writer(self._buffer,
                                  delimiter=',',
                                  quotechar='"',
                                  quoting=csv.QUOTE_MINIMAL)

        
    def write_buffer_to_s3(self):
        conn = tinys3.Connection(self._env.AMAZON_WEB_SERVICES_ACCESS_KEY,
                                 self._env.AMAZON_WEB_SERVICES_SECRET_KEY)
        content = BytesIO(self._buffer.getvalue().encode('utf-8'))
        conn.upload(self._manifest_file, content, SCRAPER_TO_LOAD)

        manifest_content = BytesIO(StringIO(json.dumps(
            {"entries": [{"url": "s3://{0}/{1}".format(
                self._env.RAVANA_S3_BUCKET,
                self._table_file)}]}).getvalue().encode('utf-8')))
        conn.upload(self._manifest_file, manifest_content, SCRAPER_TO_LOAD)

        
    def load_data(self, session):
        records = session.query(self._table_obj).all()
        self._write.writerow([ column.name for column in self._table_obj.__mapper__.columns ])
        for rec in records:
            self._write.writerow([ getattr(rec, column.name)
                                   for column in self._table_obj.__mapper__.columns ])
