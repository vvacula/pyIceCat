from IceCat import IceCat

import logging

# setup temp data directory, output file name, auth info
data_dir = '_fullcatalog_data/'
auth = ('westskeld', 'nUF7cTbQHUD7')
output_file = 'fullcatalog.json'

# specify additional product detail keys
detail_keys = [
               # 'ProductDescription[@LongDesc]',
               'ShortSummaryDescription',
               'LongSummaryDescription',
               'ProductDescription[@ShortDesc]',
               'Product',
               # 'Product[@Name]', 'Product[@ReleaseDate]', 'ThumbPic', 'ThumbPicSize', 'Title',
              ]

# create the catalog instance.
# this will pull reference files, and the daily produc index file
logger = logging.getLogger('')
logger.setLevel(logging.DEBUG)
catalog = IceCat.IceCatCatalog(data_dir=data_dir, auth=auth, log=logger, fullcatalog=True)

# save the results to a JSON file
catalog.categories.dump_categories_to_file()

# add product details
# this will download and parse individual product XML for
# every item listed in the daily file
catalog.add_product_details_parallel(keys=detail_keys, connections=10)

# save the results to a JSON file
catalog.dump_to_file(output_file)
