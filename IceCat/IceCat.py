# use C implementation for speed
import xml.etree.cElementTree as ET

import json
import xmltodict
import progressbar

import requests
import gzip
import os
import sys
import collections

from multiprocessing import Process, Queue

from IceCat import bulk_downloader
import pprint
import re
import codecs

# CZECH langid = 15 (See list in "CategoriesList.xml")
# Some categories might not be defined for CZECH.
# Using ENGLISH only, it applies only on Categories, not products itself
langid = "1"
'''
Process only English data
'''


class IceCat(object):
    '''

    Base Class for all Ice Cat Mappings. Do not call this class directly.

    :param log: optional logging.getLogger() instance
    :param xml_file: XML product index file. If None the file will be downloaded from the Ice Cat web site.
    :param auth: Username and password touple, as needed for Ice Cat website authentication
    :param data_dir: Directory to hold downloaded reference and product xml files


    '''

    def __init__(self, log=None, xml_file=None, auth=('user', 'passwd'), data_dir='_data/'):
        self.log = log
        if not log:
            import logging
            self.log = logging.getLogger()

        self.auth = auth

        self.data_dir = data_dir

        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)

        if xml_file is None:
            xml_file = self._download()

        if os.path.isfile(xml_file):
            self._parse(xml_file)
        else:
            self.log.error("File does not exist {}".format(xml_file))
            return False

    def _download(self):

        self.log.info("Downloading {} from {}".format(self.TYPE, self.baseurl + self.FILENAME))

        # save the response in the data dir before parsing
        self.local_file = self.data_dir + os.path.basename(self.FILENAME)
        res = requests.get(self.baseurl + self.FILENAME, auth=self.auth, stream=True)
        with open(self.local_file, 'wb') as f:
            for chunk in res.iter_content(chunk_size=1024):
                if chunk:
                    f.write(chunk)
            f.closed
        self.log.debug("Got headers: {}".format(res.headers))

        if 200 <= res.status_code < 299:
            # handle gzipped files
            return self.local_file
            # if self.local_file.endswith('.gz'):
            #     with gzip.open(self.local_file, 'rb') as f:
            #         return ET.parse(f).getroot()
            # else:
            #         return ET.parse(self.local_file).getroot()
        else:
            self.log.error("Did not receive good status code: {}".format(res.status_code))
            return False


class IceCatSupplierMapping(IceCat):
    '''
    Create a dict of product supplier IDs to supplier names

    Refer to IceCat class for arguments

    '''

    baseurl = 'https://data.icecat.biz/export/freeurls/'
    FILENAME = 'supplier_mapping.xml'
    TYPE = 'Supplier Mapping'

    def _parse(self, xml_file):

        if xml_file:
            data = ET.parse(xml_file).getroot()
        else:
            self.log.error("Failed to retrieve suppliers")
            return False

        '''
        Data is an XML ElementTree Object
        '''
        self.id_map = {}
        self.catid = ''
        self.catname = ''

        for elem in data.iter('SupplierMapping'):
            self.mfrid = elem.attrib['supplier_id']
            self.mfrname = elem.attrib['name']
            if not self.mfrname:
                self.mfrname = "Unknown"
            self.id_map[self.mfrid] = self.mfrname
        self.log.info("Parsed {} Manufacturers from IceCat Supplier Map".format(str(len(self.id_map.keys()))))

    def get_mfr_byId(self, mfr_id):
        '''
        Return a Product Supplier or False if no match

        :param mfr_id: Supplier ID
        '''

        if mfr_id in self.id_map:
            return self.id_map[mfr_id]
        return False


class IceCatCategoryMapping(IceCat):
    '''
    Create a dict of product category IDs to category names

    Refer to IceCat class for arguments

    '''
    baseurl = 'https://data.icecat.biz/export/freexml/refs/'
    FILENAME = 'CategoriesList.xml.gz'
    TYPE = 'Categories List'

    def _parse(self, xml_file):
        if xml_file.endswith('.gz'):
            with gzip.open(xml_file, 'rb') as f:
                data = ET.parse(f).getroot()
        else:
            data = ET.parse(xml_file).getroot()

        '''
        Data is an XML ElementTree Object
        '''
        self.id_map = {}
        self.catid = ''
        self.catname = ''
        self.findpath = 'Name[@langid="' + langid + '"]'
        for elem in data.iter('Category'):
            self.catid = elem.attrib['ID']
            for name in elem.iterfind(self.findpath):
                self.catname = name.attrib['Value']
                # only need one match
                break
            if not self.catname:
                self.catname = "Unknown"
            self.id_map[self.catid] = self.catname
        self.log.info("Parsed {} Categories from IceCat CategoriesList".format(str(len(self.id_map.keys()))))

    def get_cat_byId(self, cat_id):
        '''
        Return a Product Category or False if no match

        :param cat_id: Category ID
        '''
        if cat_id in self.id_map:
            return self.id_map[cat_id]
        return False

    def dump_categories_to_file(self, filename=None):
        '''
        Save CategoriesList to a JSON file

        :param filename: File name
        '''
        if filename:
            self.categories_file = filename
        else:
            self.categories_file = 'categories.json'

        with open(self.categories_file, 'w') as f:
            f.write(json.dumps(self.id_map, indent=2))
        self.log.info("CategoriesList output written to {}".format(self.categories_file))
        f.closed


class IceCatProductDetails(IceCat):
    '''
    Extract product detail data. It's unusual to call this class directly. Used by add_product_details..()

    :param keys: a list of product detail keys. Refer to Basic Usage Example
    :param cleanup_data_files: whether to delete xml files after parsing.
    :param filename: xml file with the product details


    Refer to IceCat class for additional arguments

    '''

    def __init__(self, keys, cleanup_data_files=True, filename=None, *args, **kwargs):
        self.keys = keys
        self.FILENAME = filename
        self.cleanup_data_files = cleanup_data_files
        super(IceCatProductDetails, self).__init__(*args, **kwargs)

    baseurl = 'https://data.icecat.biz/'
    TYPE = 'Product details'

    o = {}

    def _parse(self, xml_file):
        self.xml_file = xml_file
        data = ET.parse(xml_file).getroot()

        # for elem in data.iter('Product'):
        for attribute in self.keys:
            if '@' in attribute:
                attr = attribute[attribute.index("@") + 1:attribute.rindex("]")]
                q = data.find('./*' + attribute)
                if q is not None:
                    self.o.update({attr.lower(): q.attrib[attr]})
            else:
                for name in data.iter(attribute):
                    textname = name.text
                    if re.findall('\w+', textname):
                        self.o.update({attribute.lower(): name.text})
                    else:
                        for i in name.attrib:
                            self.o.update({i.lower(): name.attrib[i]})

        self.log.debug("Parsed product details for {}".format(xml_file))
        if self.cleanup_data_files:
            try:
                os.remove(xml_file)
            except:
                self.log.warning("Unable to delete temp file {}".format(xml_file))

    def get_data(self):
        return self.o


class IceCatCatalog(IceCat):
    '''
    Parse Ice Cat catalog index file.
    Special handling of the input data is based on IceCAT OCI Revision date: April 24, 2015, Version 2.46:
         - resolve supplier ID, and Category ID to their english names
         - unroll ean_upcs nested structure to flat value, or list
         - convert attribute names according to the table (to lower case)
         - drop keys in the exclude_list, default ['Country_Markets']
         - discard parent layers above 'file' key

    :param suppliers: IceCatSupplierMapping object. If None specified a mapping is instantiated inside the class.
    :param categories: IceCatCategoryMapping object. If None specified a mapping is instantiated inside the class.
    :param exclude_keys: a list of keys to omit from the product index.
    :param fullcatalog: Set to True to download full product catalog. 64-bit python is required for this option
                        because of >2GB memory footprint. You will need ~4.5 GB of virtual memory to process a 500k
                        item catalog.

    Refer to IceCat class for additional arguments
    '''

    def __init__(self, suppliers=None, categories=None, exclude_keys=['Country_Markets'], fullcatalog=False, *args, **kwargs):
        self.suppliers = suppliers
        self.categories = categories

        self.exclude_keys = exclude_keys
        if fullcatalog:
            self.FILENAME = 'files.index.xml'
        else:
            self.FILENAME = 'daily.index.xml'
        super(IceCatCatalog, self).__init__(*args, **kwargs)

    baseurl = 'https://data.icecat.biz/export/freexml/CZ/'
    TYPE = 'Catalog Index'

    _namespaces = {
        'Product_ID': 'product_id',
        'Updated': 'updated',
        'Quality': 'quality',
        # 'Supplier_id': 'supplier_id',
        'Prod_ID': 'prod_id',
        'Catid': 'catid',
        'On_Market': 'on_market',
        'Model_Name': 'model_name',
        'Product_View': 'product_view',
        'HighPic': 'highpic',
        'HighPicSize': 'highpicsize',
        'HighPicWidth': 'highpicwidth',
        'HighPicHeight': 'highpicheight',
        'ReleaseDate': 'release_date',
        'Name': 'name',
        'Title': 'title',
        # 'ThumbPic': 'thumbpic',
        # 'ThumbPicSize': 'thumbpicsize',
        # 'ThumbPicWidth': 'thumbpicwidth',
        # 'ThumbPicHeight': 'thumbpicheight',
        # 'Date_Added': 'date_added',
        # 'EndOfLifeDate': 'endoflife_date',
        # 'VirtualCategory': 'virtual_category',
        # 'ProductFamily': 'product_family',
        # 'ProductSeries': 'product_series',
        # 'Brand': 'brand',
        # 'LongProductName': 'long_productname',
    }

    '''
    Process only selected categories to save up some memory and space.
    '''
    _categories = {
        # "153": "testing category for development",
        "588": "Camera Lenses",
        "933": "Camera Lens Adapters",
        # "1560": "Lens Hoods",
        # "1682": "Camera Lens Filters",
        # "1726": "Lens Caps",
        "3353": "Mobile Phone Lenses",
        # "5543": "Lens Flipper Holders",
        # "5824": "Camera Lens Cases",
        "7229": "Camera Lens Docks",
        "571": "Cameras & Camcorders",
        "575": "Digital Cameras",
        # "585": "Camera Accessories",
        # "586": "Camera Flashes",
        # "934": "Camera Cables",
        "1179": "Camera Docks",
        "1288": "Camera Kits",
        # "1290": "Camera Cases",
        "1918": "Instant Print Cameras",
        # "2259": "Digital Camera Battery Grips",
        "2287": "Action Sports Cameras",
        # "2469": "Camera Cages",
        # "2470": "Camera Mounting Accessories",
        # "2485": "Camera Flash Accessories",
        # "2682": "Camera Raincovers",
        # "3242": "Camera Remote Controls",
        # "3245": "Camera Monopods",
        # "3320": "Camera Eyecups",
        # "3363": "Camera Rigs",
        # "4691": "Camera Viewfinders",
        # "5080": "Camera Filter Accessories",
        # "5873": "Camera Clamps",
        # "5935": "Camera Filter Cases",
        # "117": "Telephones",
        "119": "Mobile Phones",
        "1089": "Mobile Phone Starter Kits",
        # "1490": "Mobile Phone Faceplates",
        # "1497": "Mobile Phone Cables",
        "1893": "Smartphones",
        # "2779": "Mobile Phone Cases",
        # "5392": "Mobile Phone Flashes",
    }

    def _postprocessor(self, path, key, value):
        if key == "file":
            # '''
            # Look up supplier id
            # '''
            # try:
                # value.update({'supplier': self.suppliers.get_mfr_byId(value['supplier_id'])})
            # except:
                # self.log.warning("Unable to find supplier for supplier_id: {}".format(value['supplier_id']))

            '''
            Look up category
            '''
            try:
                value.update({'category': self.categories.get_cat_byId(value['catid']).title()})
            except:
                self.log.warning("Unable to find category for catid: {}".format(value['catid']))

            # unroll EAN_UPCS. sometimes this is a list of single value dicts, other times it's a string.
            if 'EAN_UPCS' in value:
                try:
                    value['EAN_UPCS'] = [value['EAN_UPCS']['EAN_UPC']['Value']]
                except TypeError:
                    upcs = []
                    for item in value['EAN_UPCS']['EAN_UPC']:
                        upcs.append(list(item.values())[0])
                    value['EAN_UPCS'] = upcs
                except:
                    # something bad happened with upcs
                    self.log.warning("Unable to unroll EAN_UPCS {} for product_id: {}".format(
                        sys.exc_info(), value['product_id']))

            self.key_count += 1
            self.bar.update(self.key_count)

        # skip keys we are not interested in.
        elif key in self.exclude_keys:
            return None

        return key.lower(), value

    # used to flatten a nested structure if needed

    def _flatten(self, d, parent_key='', sep='_'):
        items = []
        for k, v in d.items():
            new_key = parent_key + sep + k if parent_key else k
            if isinstance(v, collections.MutableMapping):
                items.extend(self._flatten(v, new_key, sep=sep).items())
            else:
                items.append((new_key, v))
        return dict(items)

    def _parse(self, xml_file):
        self.xml_file = xml_file
        self.key_count = 0

        # skip suppliers for now, there were some problems with downloading xml file
        # if not self.suppliers:
            # self.suppliers = IceCatSupplierMapping(log=self.log, auth=self.auth, data_dir=self.data_dir)
        if not self.categories:
            self.categories = IceCatCategoryMapping(log=self.log, data_dir=self.data_dir, auth=self.auth)

        print("Parsing products from index file:", xml_file)
        with progressbar.ProgressBar(max_value=progressbar.UnknownLength) as self.bar:
            with open(self.xml_file, 'rb') as f:
                self.o = xmltodict.parse(f, attr_prefix='', postprocessor=self._postprocessor,
                                         namespace_separator='', process_namespaces=True, namespaces=self._namespaces)
            f.closed

            # peel down to file key
            self.o = self.o['icecat-interface']['files.index']['file']
            self.log.info("Parsed {} products from IceCat catalog".format(str(len(self.o))))
        return len(self.o)

    def add_product_details_parallel(self, keys=['ProductDescription'], connections=5):
        '''
        Download and parse product details, using threads.

        :param keys: List of Ice Cat product detail XML keys to include in the output.  Refer to Basic Usage Example.
        :param connections: Number of simultanious download threads.  Do not go over 100.
        '''
        self.keys = keys
        self.connections = connections
        baseurl = 'https://data.icecat.biz/'
        TYPE = 'Product details'
        urls = []

        xml_dir = self.data_dir + 'product_xml/'

        if not os.path.exists(xml_dir):
            os.makedirs(xml_dir)

        # Process only selected categories, skip all the others
        self.o = list(filter(lambda item: (item['catid'] in self._categories) , self.o))

        for item in self.o:
            urls.append(baseurl + item['path'].encode('latin-1').decode())
        self.log.info("Downloading detail data with {} connections".format(self.connections))

        download = bulk_downloader.fetchURLs(log=self.log, urls=urls, auth=self.auth,
                                             connections=self.connections,
                                             data_dir=xml_dir)

        self.key_count = 0
        print("Parsing product details:")
        with progressbar.ProgressBar(max_value=len(self.o)) as self.bar:
            for item in self.o:
                xml_file = xml_dir + os.path.basename(item['path'])
                self.key_count += 1
                self.bar.update(self.key_count)
                try:
                    product_detais = IceCatProductDetails(xml_file=xml_file, keys=self.keys,
                                                          auth=self.auth, data_dir=xml_dir, log=self.log, cleanup_data_files=False)
                    item.update(product_detais.get_data())
                except:
                    self.log.error(
                        "Could not obtain product details from IceCat for product_id {}".format(item['path']))

    def add_product_details(self, keys=['ProductDescription']):
        '''
        Download and parse product details.  Use add_product_details_parallel() instead, for a much improved performance.

        :param keys: List of Ice Cat product detail XML keys to include in the output.  Refer to Basic Usage Example.
        '''
        self.keys = keys
        for item in self.o:
            try:
                product_detais = IceCatProductDetails(filename=item['path'], keys=self.keys,
                                                      auth=self.auth, data_dir=self.data_dir, log=self.log)
                item.update(product_detais.get_data())
            except:
                self.log.error("Could not obtain product details from IceCat for product_id {}".format(item['path']))

    def get_data(self):
        '''
        Return ordered list of product attributes
        '''
        return self.o

    def dump_to_file(self, filename=None):
        '''
        Save product attributes to a JSON file

        :param filename: File name
        '''
        if filename:
            self.json_file = filename
        else:
            # change extension for the JSON output
            self.json_file = os.path.splitext(self.xml_file)[0] + '.json'

        with open(self.json_file, 'w') as f:
            f.write(json.dumps(self.o, indent=2))
        self.log.info("JSON output written to {}".format(self.json_file))
        f.closed