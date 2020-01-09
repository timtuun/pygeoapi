# =================================================================
#
# Authors: Timo Tuunanen <timo.tuunanen@rdvelho.com>
#
# Copyright (c) 2019 Timo Tuunanen
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation
# files (the "Software"), to deal in the Software without
# restriction, including without limitation the rights to use,
# copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following
# conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
# OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
# HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
# WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
# OTHER DEALINGS IN THE SOFTWARE.
#
# =================================================================

import logging

from pymongo import MongoClient
from pymongo import GEOSPHERE
from pymongo import ASCENDING, DESCENDING
from pymongo.collection import ObjectId
from pygeoapi.date_time import DatetimeRange
from pygeoapi.provider.base import BaseProvider
from datetime import timedelta

LOGGER = logging.getLogger(__name__)


class MongoProvider(BaseProvider):
    """Generic provider for Mongodb.
    """

    def __init__(self, provider_def):
        """
        MongoProvider Class constructor

        :param provider_def: provider definitions from yml pygeoapi-config.
                             data,id_field, name set in parent class

        :returns: pygeoapi.providers.mongo.MongoProvider
        """
        # this is dummy value never used in case of Mongo.
        # Mongo id field is _id
        provider_def.setdefault('id_field', '_id')

        BaseProvider.__init__(self, provider_def)

        LOGGER.info('Mongo source config: {}'.format(self.data))

        dbclient = MongoClient(self.data)
        self.featuredb = dbclient.get_default_database()
        self.collection = provider_def['collection']
        self.datetime_field = (provider_def['datetime_field']
                               if 'datetime_field' in provider_def
                               else 'datetime')
        self.featuredb[self.collection].create_index([("geometry", GEOSPHERE)])
        self.fields = self.get_fields()

    def _get_field_type(self, obj):
        if isinstance(obj, str):
            return 'string'
        if isinstance(obj, float):
            return 'float'
        if isinstance(obj, int):
            return 'int'

        return None

    def get_fields(self):
        """
        Get provider field information (names, types)

        :returns: dict of fields
        """
        fields = {}
        features, unused = self._get_feature_list({}, maxitems=-1)
        for feature in features:
            for key in feature['properties']:
                val = feature['properties'][key]
                val_type = self._get_field_type(val)
                if key not in fields and val_type is not None:
                    fields[key] = val_type

        # Datetime field is the only datetime type the we support in orderby
        fields['datetime'] = 'datetime'
        return fields

    def _get_feature_list(self, filterObj, sortList=[], skip=0, maxitems=1):
        featurecursor = self.featuredb[self.collection].find(filterObj)

        if sortList:
            featurecursor = featurecursor.sort(sortList)

        matchCount = self.featuredb[self.collection].count_documents(filterObj)
        featurecursor.skip(skip)
        if maxitems > 0:
            featurecursor.limit(maxitems)
        featurelist = list(featurecursor)
        for item in featurelist:
            item['id'] = str(item.pop('_id'))

        return featurelist, matchCount

    def _get_sort_property_name(self, sort):
        name = sort['property']
        if name == 'datetime':
            return "properties." + self.datetime_field
        else:
            return "properties." + name

    def query(self, startindex=0, limit=10, resulttype='results',
              bbox=[], datetime=None, properties=[], sortby=[]):
        """
        query the provider

        :returns: dict of 0..n GeoJSON features
        """
        and_filter = []

        if len(bbox) == 4:
            x, y, w, h = map(float, bbox)
            and_filter.append(
                {'geometry': {'$geoWithin': {'$box': [[x, y], [w, h]]}}})

        if datetime is not None:
            assert isinstance(datetime, DatetimeRange)
            ''' Mongo can only handle milliseconds in date queries, so we have
                to make a conversion. Lets assume, we have a query:
                datetime=2019-11-14T11:16:02.989000
                This will lead to following DatetimeRange:
                datetime.start = 2019-11-14T11:16:02.989000
                datetime.end = 2019-11-14T11:16:02.989001
                , that Mongo iterprets as 11:16:02.989 for both start and end.

                DatetimeRange is converted to following datetimes:
                datetime.start = 2019-11-14T11:16:02.989000
                datetime.end = 2019-11-14T11:16:02.990001
                , that Mongo can handles as
                datetime.start = 2019-11-14T11:16:02.989
                datetime.end = 2019-11-14T11:16:02.990
            '''
            end = datetime.end
            if end is not None and end.microsecond % 1000:
                end += timedelta(milliseconds=1)

            if datetime.start is not None and datetime.end is not None:
                and_filter.append(
                    {'properties.' + self.datetime_field:
                     {'$gte': datetime.start, '$lt': end}})
            elif datetime.start is not None:
                and_filter.append(
                    {'properties.' + self.datetime_field:
                     {'$gte': datetime.start}})
            elif datetime.end is not None:
                and_filter.append(
                    {'properties.' + self.datetime_field: {'$lt': end}})
            else:
                raise ValueError('DatetimeRange begin and end are None')

        for prop in properties:
            and_filter.append({"properties." + prop[0]: {'$eq': prop[1]}})

        filterobj = {'$and': and_filter} if and_filter else {}

        sort_list = [(self._get_sort_property_name(sort),
                      ASCENDING if (sort['order'] == 'A') else DESCENDING)
                     for sort in sortby]

        featurelist, matchcount = self._get_feature_list(filterobj,
                                                         sortList=sort_list,
                                                         skip=startindex,
                                                         maxitems=limit)

        if resulttype == 'hits':
            featurelist = []

        feature_collection = {
            'type': 'FeatureCollection',
            'features': featurelist,
            'numberMatched': matchcount,
            'numberReturned': len(featurelist)
        }

        return feature_collection

    def get(self, identifier):
        """
        query the provider by id

        :param identifier: feature id
        :returns: dict of single GeoJSON feature
        """
        featurelist, matchcount = self._get_feature_list(
                                    {'_id': ObjectId(identifier)})
        return featurelist[0] if featurelist else None

    def create(self, new_feature):
        """Create a new feature
        """
        self.featuredb[self.collection].insert_one(new_feature)

    def update(self, identifier, updated_feature):
        """Updates an existing feature id with new_feature

        :param identifier: feature id
        :param new_feature: new GeoJSON feature dictionary
        """
        data = {k: v for k, v in updated_feature.items() if k != 'id'}
        self.featuredb[self.collection].update_one(
            {'_id': ObjectId(identifier)}, {"$set": data})

    def delete(self, identifier):
        """Delets an existing feature

        :param identifier: feature id
        """
        self.featuredb[self.collection].delete_one(
            {'_id': ObjectId(identifier)})