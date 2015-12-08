# -*- coding: utf-8 -*-

import csv
from io import BytesIO, StringIO
import json
import os

import tinys3


### dict/key with environment!
### 
### 

SCRAPER_TO_LOAD = 'scraper_to_load'


class S3Table:
    
    def __init__(self, table_obj, scrape_id, env):
        self._table_obj = table_obj
        self._scrape_id = scrape_id
        self._env = env
        ##
        self.amazon_web_services_access_key = self._env.get('AMAZON_WEB_SERVICES_ACCESS_KEY')
        self.amazon_web_services_secret_key = self._env.get('AMAZON_WEB_SERVICES_SECRET_KEY')
        self.ravana_s3_bucket = self._env.get('RAVANA_S3_BUCKET')

        ##
        self._tablename = table_obj.__tablename__
        self._table_file = '{0}-{1}.csv'.format(self._tablename, self._scrape_id)
        self._manifest_file = 'l2wr_{0}_{1}_manifest'.format(self._tablename,
                                                             self._scrape_id)
        ##
        self._buffer = StringIO()
        self._writer = csv.writer(self._buffer,
                                  delimiter='\t')

        
    def write_buffer_to_s3(self):
        conn = tinys3.Connection(self.amazon_web_services_access_key,
                                 self.amazon_web_services_secret_key)
        content = BytesIO(self._buffer.getvalue().encode('utf-8'))
        conn.upload(os.path.join(SCRAPER_TO_LOAD, self._table_file),
                    content,
                    self.ravana_s3_bucket)

        manifest_content = BytesIO(StringIO(json.dumps(
            {"entries": [{"url": "s3://{0}/{1}/{2}".format(
                self.ravana_s3_bucket,
                SCRAPER_TO_LOAD,
                self._table_file)}]})).getvalue().encode('utf-8'))
        conn.upload(os.path.join(SCRAPER_TO_LOAD, self._manifest_file),
                    manifest_content,
                    self.ravana_s3_bucket)

        
    def load_data(self, session):
        records = session.query(self._table_obj).all()
        #self._writer.writerow([ column.name for column in self._table_obj.__mapper__.columns ])
        for rec in records:
            self._writer.writerow([ getattr(rec, column.name)
                                    for column in self._table_obj.__mapper__.columns ])
