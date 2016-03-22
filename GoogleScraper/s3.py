# -*- coding: utf-8 -*-

import csv
from io import BytesIO, StringIO
import json
import os
from datetime import datetime

import tinys3


### dict/key with environment!
### 
### 

SCRAPER_TO_LOAD = 'scraper_to_load'
NULL_STRING='null_string'


class S3Table:
    
    def __init__(self, table_obj, scrape_id, env):
        self._table_obj = table_obj
        self._scrape_id = scrape_id
        self.AMAZON_WEB_SERVICES_ACCESS_KEY = env.get('AMAZON_WEB_SERVICES_ACCESS_KEY')
        self.AMAZON_WEB_SERVICES_SECRET_KEY = env.get('AMAZON_WEB_SERVICES_SECRET_KEY')
        self.RAVANA_S3_BUCKET = env.get('RAVANA_S3_BUCKET')

        ##
        self._tablename = table_obj.__tablename__
        self._table_file = '{0}-{1}.csv'.format(self._tablename, self._scrape_id)
        self._manifest_file = 'l2wr_{0}_{1}_manifest'.format(self._tablename,
                                                             self._scrape_id)
        ##
        self._buffer = StringIO()
        self._writer = csv.writer(self._buffer,
                                  delimiter='\t')


    def _None_to_string(self, value):
        if value is None:
            return NULL_STRING
        else:
            return value
        
        
    def write_buffer_to_s3(self):
        conn = tinys3.Connection(self.AMAZON_WEB_SERVICES_ACCESS_KEY,
                                 self.AMAZON_WEB_SERVICES_SECRET_KEY)
        content = BytesIO(self._buffer.getvalue().encode('utf-8'))
        conn.upload(os.path.join(SCRAPER_TO_LOAD, self._table_file),
                    content,
                    self.RAVANA_S3_BUCKET)
        
        manifest_content = BytesIO(StringIO(json.dumps(
            {"entries": [{"url": "s3://{0}/{1}/{2}".format(
                self.RAVANA_S3_BUCKET,
                SCRAPER_TO_LOAD,
                self._table_file)}]})).getvalue().encode('utf-8'))
        conn.upload(os.path.join(SCRAPER_TO_LOAD, self._manifest_file),
                    manifest_content,
                    self.RAVANA_S3_BUCKET)

        
    def load_data(self, session):
        records = session.query(self._table_obj).all()
        #self._writer.writerow([ column.name for column in self._table_obj.__mapper__.columns ])
        for rec in records:
            self._writer.writerow([ self._None_to_string(getattr(rec, column.name))
                                    for column in self._table_obj.__mapper__.columns ])


def get_s3_conn(env):
    return = tinys3.Connection(env.get('AMAZON_WEB_SERVICES_ACCESS_KEY'),
                               env.get('AMAZON_WEB_SERVICES_SECRET_KEY'))


def store_serp_in_s3(serp, scrape_id, keyword, env, conn=None):
    if not conn:
        conn = get_s3_conn(env)
    content = BytesIO(serp.encode('utf-8'))
    filename = "{scrape_id}_{keyword}_{time}.html".format(
        scrape_id=scrape_id,
        keyword=keyword,
        time=str(datetime.now()).replace(" ", "_"))
    conn.upload(filename,
                content,
                env.get('L2WR_SERPS'))
