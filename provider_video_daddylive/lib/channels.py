"""
MIT License

Copyright (C) 2023 ROCKY4546
https://github.com/rocky4546

This file is part of Cabernet

Permission is hereby granted, free of charge, to any person obtaining a copy of this software
and associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute,
sublicense, and/or sell copies of the Software, and to permit persons to whom the Software
is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or
substantial portions of the Software.
"""

import base64
import html
import importlib
import importlib.resources
import json
import os
import re
import time
import urllib.parse

from lib.plugins.plugin_channels import PluginChannels
from lib.common.decorators import handle_json_except
from lib.common.decorators import handle_url_except
import lib.common.utils as utils
from ..lib import daddylive
from .. import resources

class Channels(PluginChannels):

    def __init__(self, _instance_obj):
        super().__init__(_instance_obj)

        self.pnum = self.config_obj.data[self.config_section]['player-number']
        self.search_url = None
        self.search_m3u8 = None
        self.search_ch = re.compile(r'div class="grid-item">'
                                    + r'<a href=\"(\D+(\d+).php.*?)\" target.*?<strong>(.*?)</strong>')
        self.groups_other = None
        self.ch_db_list = None

    def get_channels(self):
        self.ch_db_list = self.db.get_channels(self.plugin_obj.name, self.instance_key)

        ch_list = self.get_channel_list()
        if ch_list is None or len(ch_list) == 0:
            self.logger.warning('DaddyLive channel list is empty from provider, not updating Cabernet')
            return
        self.logger.info("{}: Found {} stations on instance {}"
                         .format(self.plugin_obj.name, len(ch_list), self.instance_key))
        ch_list.sort(key=lambda d: d['display_name'].casefold())
        ch_num = 1
        for ch in ch_list:
            ch['number'] = ch_num
            ch_num += 1
        return ch_list

    @handle_url_except(timeout=10.0)
    @handle_json_except
    def get_channel_ref(self, _channel_id):
        """
        gets the referer required to obtain the ts or stream files from server
        """
        self.pnum = self.config_obj.data[self.config_section]['player-number']
        self.search_url = re.compile(self.plugin_obj.unc_daddylive_dl21a[self.pnum])
        url = self.plugin_obj.unc_daddylive_base + \
            self.plugin_obj.unc_daddylive_stream[self.pnum].format(_channel_id)
        header = {'User-agent': utils.DEFAULT_USER_AGENT,
                  'Referer': url,
                  }
        text = self.get_uri_data(url, 2, header)
        if not text:
            self.logger.info('{}: 1 Unable to obtain url for channel {}, aborting'
                             .format(self.plugin_obj.name, _channel_id))
            return

        player_ch = re.compile(b'role="button">Player ([0-9])</button>')
        m=re.findall(player_ch, text)
        if m:
            self.groups_other = ['']
            for p in m:
                self.groups_other[0] += 'P' + p.decode('utf-8')
        else:
            self.groups_other = None

        data = self.db.get_channel(_channel_id, self.plugin_obj.name, self.instance_key)
        if data:
            ch_json = data['json']
        else:
            ch_json = None
        m = re.search(self.search_url, text)

        if not m:
            # unable to obtain the url, try using the previous ref url
            self.logger.info('{}: 2 Unable to obtain url for channel {} from provider'
                             .format(self.plugin_obj.name, _channel_id))

            if ch_json and ch_json.get('channel_ref'):
                ch_url = ch_json['channel_ref'].get(str(self.pnum))
                if ch_url:
                    self.logger.debug('Reusing reference url previously {}'.format(ch_url))
                    json_updated = self.update_header(ch_json, ch_url)
                    if json_updated:
                        self.db.update_channel_json(ch_json, self.plugin_obj.name, self.instance_key)
                    return ch_url
                else:
                    self.logger.notice('1 Unable to obtain url from provider, aborting')
            else:
                self.logger.notice('2 Unable to obtain url from provider, aborting')
            return

        ch_url = m[2].decode('utf8')

        if self.pnum > 3:
            header = {'User-agent': utils.DEFAULT_USER_AGENT,
                      'Referer': self.plugin_obj.unc_daddylive_base,
                      'Connection' : 'Keep-Alive'
                      }
            text = self.get_uri_data(ch_url, 2, header)
            try:
                c_url2=re.search(b'iframe src="([^"]+)', text)[1].decode('utf8')
            except TypeError as ex:
                self.logger.notice('{} Player {} not available for Channel {}, aborting' \
                    .format(self.plugin_obj.name, self.pnum, _channel_id))
                return
            ch_url = c_url2
        
        json_updated = self.update_header(ch_json, ch_url)

        if ch_json:
            ch_ref = ch_json.get('channel_ref')
            if not ch_ref:
                self.logger.debug('1 Adding channel ref to db for player {}'.format(self.pnum))
                ch_json.update({'channel_ref': {str(self.pnum): ch_url}})
                json_updated = True
            elif ch_ref.get(str(self.pnum)):
                # channel_ref present, if the same, ignore otherwise update db
                if ch_ref.get(str(self.pnum)) != ch_url:
                    ch_json['channel_ref'][str(self.pnum)] = ch_url
                    json_updated = True
            else:
                # pnum for ref not present, save data to db
                self.logger.debug('2 Adding channel ref to db for player {}'.format(self.pnum))
                ch_json['channel_ref'].update({str(self.pnum): ch_url})
                json_updated = True
            if json_updated:
                self.db.update_channel_json(ch_json, self.plugin_obj.name, self.instance_key)
        return ch_url

    def update_header(self, _ch_json, _ch_url):
        # return the updated json (by reference) and return if it changed.
        json_updated = False
        parsed_url = urllib.parse.urlsplit(_ch_url)
        ref_url = parsed_url.scheme + '://' + parsed_url.netloc + '/'
        if _ch_json:
            h = _ch_json.get('Header')
            if h:
                r = h.get('Referer')
                if ref_url != r:
                    _ch_json['Header']['Referer'] = ref_url
                    _ch_json['Header']['Origin'] = ref_url[:-1]
                    _ch_json['ref_url'] = ref_url
                    json_updated = True
                    self.logger.notice('{}: Player changed for channel {}.  Data has been reset'.format(self.plugin_obj.name, _ch_json['id']))
        return json_updated

    @handle_url_except(timeout=10.0)
    @handle_json_except
    def get_channel_uri(self, _channel_id):
        self.pnum = self.config_obj.data[self.config_section]['player-number']
        self.search_m3u8 = re.compile(self.plugin_obj.unc_daddylive_dl25f[self.pnum])
        self.logger.debug('{}: CHID: {} Using Player {}'.format(self.plugin_obj.name, _channel_id, self.pnum))
        json_needs_updating = False
        ch_url = self.get_channel_ref(_channel_id)
        if not ch_url:
            return

        header = {
            'User-agent': utils.DEFAULT_USER_AGENT,
            'Referer': self.plugin_obj.unc_daddylive_base + self.plugin_obj.unc_daddylive_stream[self.pnum].format(_channel_id)}
        text = self.get_uri_data(ch_url, 2, _header=header)
        if text is None:
            self.logger.notice('{}: {} #1 Unable to obtain m3u8, possible player num not available for channel, aborting'
                               .format(self.plugin_obj.name, _channel_id))
            return

        stream_url = self.find_m3u8(text, _channel_id, header, ch_url)
        if not stream_url:
            return

        if self.config_obj.data[self.config_section]['player-stream_type'] == 'm3u8redirect':
            self.logger.warning('{}:{} Stream Type of m3u8redirect not available with this plugin'
                .format(self.plugin_obj.name, self.instance_key))
            return stream_url

        parsed_url = urllib.parse.urlsplit(ch_url)
        ref_url = parsed_url.scheme + '://' + parsed_url.netloc + '/'

        m3u8_uri = self.get_best_stream(stream_url, 2, _channel_id, ref_url)
        if not m3u8_uri:
            return stream_url
        return m3u8_uri

    def get_channel_list(self):
        ch_list = []
        results = []

        # first get the list of channels to get from the epg plugin
        tvg_list = self.get_tvg_reference()
        epg_plugins = {u['plugin']: u for u in tvg_list}.keys()
        for plugin in epg_plugins:
            zones = {u['zone']: u for u in tvg_list if u['plugin'] == plugin}.keys()
            for zone in zones:
                ch_ids = [n['id'] for n in tvg_list if n['zone'] == zone]
                chs = self.plugin_obj.plugins[plugin].plugin_obj \
                    .get_channel_list_ext(zone, ch_ids)
                if chs is None:
                    return
                ch_list.extend(chs)

        # Get the list of channels daddylive provides by channel name
        uri = self.plugin_obj.unc_daddylive_base + self.plugin_obj.unc_daddylive_channels
        text = self.get_uri_data(uri, 2)
        if text is None:
            return
        text = text.decode()
        if text is None:
            return
        text = text.replace('\n', ' ')
        text_list = re.findall(r'<label for="tab-([2-9]|[1-9][0-9])">(.*?)</center>\s*</div>\s*</div>', text)
        text_combine = ''
        for i in range(len(text_list)):
            text_combine = text_combine + text_list[i][1]
        match_list = re.findall(self.search_ch, text_combine)
        # url, id, name
        for m in match_list:
            self.groups_other = None
            if len(m) != 3:
                self.logger.warning(
                    'get_channel_list - DaddyLive channel extraction failed. Extraction procedure needs updating')
                return None
            uid = m[1]
            name = html.unescape(m[2]).strip()
            if name.lower().startswith('the '):
                name = name[4:]
            group = None
            channel_ref = None

            ch = [d for d in tvg_list if d['name'] == name]
            if len(ch):
                tvg_id = ch[0]['id']
                ch = [d for d in ch_list if d['id'] == tvg_id]
                if len(ch):
                    ch = ch[0]
                    ch_db_data = self.ch_db_list.get(uid)
                    if ch_db_data is not None:
                        ch['enabled'] = ch_db_data[0]['enabled']
                        ch['id'] = ch_db_data[0]['uid']
                        ch['name'] = name
                        ch['display_name'] = ch_db_data[0]['display_name']
                        ch['HD'] = ch_db_data[0]['json']['HD']
                        if ch_db_data[0]['json']['thumbnail'] == ch['thumbnail']:
                            thumb = ch_db_data[0]['json']['thumbnail']
                            thumb_size = ch_db_data[0]['json']['thumbnail_size']
                        else:
                            thumb = ch['thumbnail']
                            thumb_size = self.get_thumbnail_size(thumb, 2, uid)
                        ch['thumbnail'] = thumb
                        ch['thumbnail_size'] = thumb_size
                    else:
                        ch['id'] = uid
                        ch['name'] = name
                        ch['display_name'] = name
                        ch['thumbnail_size'] = self.get_thumbnail_size(ch['thumbnail'], 2, uid)

                    if ch['enabled']:
                        ref_url = self.get_channel_ref(uid)
                        if not ref_url:
                            self.logger.notice('{} BAD CHANNEL found, disabling {}:{}'
                                               .format(self.plugin_obj.name, uid, name))
                            header = None
                            if self.config_obj.data[self.config_section]['player-disable_bad_channels']:
                                ch['enabled'] = 0
                        else:
                            parsed_url = urllib.parse.urlsplit(ref_url)
                            origin_url = parsed_url.scheme + '://' + parsed_url.netloc
                            ref_url = origin_url + '/'
                            header = {'User-agent': utils.DEFAULT_USER_AGENT,
                                      'Referer': ref_url,
                                      'Origin' : origin_url,
                                      'Connection' : 'Keep-Alive'
                                      }
                            data = self.db.get_channel(uid, self.plugin_obj.name, self.instance_key)
                            if data:
                                ch_json = data['json']
                                channel_ref = ch_json.get('channel_ref')
                                if channel_ref:
                                    ch['channel_ref'] = channel_ref
                    elif ch.get('Header'):
                        header = ch['Header']
                        ref_url = ch['ref_url']
                    else:
                        header = None
                        ref_url = None

                    ch['Header'] = header
                    ch['ref_url'] = ref_url
                    ch['use_date_on_m3u8_key'] = False

                    group = [n.get('group') for n in tvg_list if n['name'] == ch['name']]
                    if len(group):
                        group = group[0]

                    if self.groups_other is not None and group not in self.groups_other:
                        self.groups_other.append(group)
                    else:
                        self.groups_other = group

                    if type(self.groups_other) == list:
                        self.groups_other = '|'.join(self.groups_other)
                    ch['groups_other'] = self.groups_other
                    ch['found'] = True

                    if any(d['id'] == uid for d in results):
                        self.logger.notice('{}:{} 1 Duplicate channel UID found, ignoring uid:{} name:{}'
                            .format(self.plugin_obj.name, self.instance_key, ch['id'], ch['name']))
                    elif ch['enabled']:
                        self.logger.info('{}:{} 1 Updating Channel {}:{}'
                            .format(self.plugin_obj.name, self.instance_key, uid, name))
                        results.append(ch)
                    else:
                        #self.logger.debug('{} 1 Skipping Channel {}:{}'.format(self.plugin_obj.name, uid, name))
                        results.append(ch)
                    continue

            if any(d['id'] == uid for d in results):
                self.logger.notice('{}:{} 2 Duplicate channel UID found, ignoring uid:{} name:{}'
                    .format(self.plugin_obj.name, self.instance_key, uid, name))
                continue

            ch_db_data = self.ch_db_list.get(uid)
            if ch_db_data is not None:
                if ch:
                    enabled = ch['enabled']
                else:
                    enabled = ch_db_data[0]['enabled']
                display_name = ch_db_data[0]['display_name']
                hd = ch_db_data[0]['json']['HD']
                thumb = ch_db_data[0]['json']['thumbnail']
                thumb_size = ch_db_data[0]['json']['thumbnail_size']
                self.groups_other = ch_db_data[0]['json']['groups_other']
                if not ch_db_data[0]['enabled']:
                    ref_url = ch_db_data[0]['json']['ref_url']
                else:
                    ref_url = self.get_channel_ref(uid)
                    if ref_url:
                        parsed_url = urllib.parse.urlsplit(ref_url)
                        ref_url = parsed_url.scheme + '://' + parsed_url.netloc + '/'
                        self.logger.info('{}:{} 2 Updating Channel {}:{}'
                            .format(self.plugin_obj.name, self.instance_key, uid, name))
            else:
                self.logger.info('{}:{} 2 New Channel Added {}:{}'
                    .format(self.plugin_obj.name, self.instance_key, uid, name))
                display_name = name
                if ch:
                    enabled = ch['enabled']
                else:
                    enabled = 1
                    ref_url = self.get_channel_ref(uid)
                    if ref_url:
                        parsed_url = urllib.parse.urlsplit(ref_url)
                        ref_url = parsed_url.scheme + '://' + parsed_url.netloc + '/'
                hd = 0
                thumb = None
                thumb_size = None
                ref_url = self.get_channel_ref(uid)
                if ref_url:
                    parsed_url = urllib.parse.urlsplit(ref_url)
                    ref_url = parsed_url.scheme + '://' + parsed_url.netloc + '/'

            data = self.db.get_channel(uid, self.plugin_obj.name, self.instance_key)
            if data:
                ch_json = data['json']
                channel_ref = ch_json.get('channel_ref')

            if not ref_url:
                header = None
            else:
                header = {'User-agent': utils.DEFAULT_USER_AGENT,
                          'Referer': ref_url,
                          'Origin' : ref_url[:-1],
                          'Connection' : 'Keep-Alive'
                          }
            if type(self.groups_other) == list:
                self.groups_other = ' | '.join(self.groups_other)
            channel = {
                'id': uid,
                'enabled': enabled,
                'callsign': uid,
                'number': 0,
                'name': name,
                'display_name': display_name,
                'HD': hd,
                'group_hdtv': None,
                'group_sdtv': None,
                'groups_other': self.groups_other,
                'thumbnail': thumb,
                'thumbnail_size': thumb_size,
                'VOD': False,
                'Header': header,
                'ref_url': ref_url,
                'use_date_on_m3u8_key': False,
                'channel_ref': channel_ref,
            }
            results.append(channel)

        found_tvg_list = [u for u in ch_list if u.get('found') is None]
        for ch in found_tvg_list:
            self.logger.notice(
                '{}:{} Channel {} from channel_list.json has different title on providers site'
                    .format(self.plugin_obj.name, self.instance_key, ch['id']))
        found_tvg_list = [u for u in ch_list if u.get('found') is not None]
        for ch in found_tvg_list:
            del ch['found']

        return results

    def get_tvg_reference(self):
        """
        Returns a list of channels with zone and channel id info
        This is sorted so we can get all channels from each zone together
        """
        if self.config_obj.data[self.plugin_obj.name.lower()]['epg-plugin'] == 'ALL':
            ch_list = json.loads(
                importlib.resources.files(resources).joinpath('channel_list.json').read_text())
            ch_list = sorted(ch_list, key=lambda d: d['zone'])
            return ch_list
        else:
            return []

    def find_m3u8(self, _text, _channel_id, _header, _ch_url):

        c_key = re.search(b'(?s) channelKey = \"([^"]*)', _text)
        if not c_key:
            # unable to obtain the url, abort
            self.logger.notice('{}: #2 Unable to obtain m3u8, possible player num not available for channel {}, aborting'
                               .format(self.plugin_obj.name, _channel_id))
            return
        c_key = c_key[1].decode('utf8')
        key_q = re.search(b'n fetchWithRetry\\(\\s*\'([^\']*)', _text)[1].decode('utf8')
        key_url = f'https://{urllib.parse.urlparse(_ch_url).netloc}{key_q}{c_key}'
        host = re.search(b'(?s)m3u8 =.*?:.*?:.*?".*?".*?"([^"]*)', _text)[1].decode('utf8')

        try:
            a_sig = re.search(b'(?s)var __e = atob\\(\\"([^"]*)', _text)[1]
            a_sig = base64.b64decode(a_sig).decode('utf8')
            a_rnd = re.search(b'(?s)var __d = atob\\(\\"([^"]*)', _text)[1]
            a_rnd = base64.b64decode(a_rnd).decode('utf8')
            a_ts = re.search(b'(?s)var __c = atob\\(\\"([^"]*)', _text)[1]
            a_ts = base64.b64decode(a_ts).decode('utf8')
            a_host = re.search(b'(?s)var __a = atob\\(\\"([^"]*)', _text)[1]
            a_host = base64.b64decode(a_host).decode('utf8')
            a_auth = re.search(b'(?s)var __b = atob\\(\\"([^"]*)', _text)[1]
            a_auth = base64.b64decode(a_auth).decode('utf8')

            a_url = f'{a_host}{a_auth}{c_key}&ts={a_ts}&rnd={a_rnd}&sig={a_sig}'

            parsed_url = urllib.parse.urlsplit(_ch_url)
            ref_url = parsed_url.scheme + '://' + parsed_url.netloc + '/'
            header = {
                'User-agent': utils.DEFAULT_USER_AGENT,
                'Referer': ref_url,
                'Origin': ref_url[:-1]}
            auth = self.get_uri_data(a_url, 2, header)
            #self.logger.warning('AUTHURL= {}  HEADER= {}  RESULT= {}'.format(a_url, header, auth))
        except TypeError:
            self.logger.notice('Unable to obtain auth keys, skipping')
            x = re.search(b'(?s)var player(.{150})', _text)
            if x:
                self.logger.debug('keys: {}'.format(x[1]))

        header = {
            'User-agent': utils.DEFAULT_USER_AGENT,
            'Referer': _ch_url}

        text = self.get_uri_data(key_url, 2, header)
        m = re.search(b':"([^"]*)', text)
        try:
            s_key = m[1].decode('utf8')
        except TypeError:
            self.logger.warning('{}: Section key for uid {} is not available, aborting'.format(self.plugin_obj.name, _channel_id))
            return
        s_key = m[1].decode('utf8')

        m3u8_url = 'https://{}{}{}/{}/mono.m3u8'.format(s_key, host, s_key, c_key)
        return m3u8_url

    GLOBAL_A = ""
    def decode_data(self, a, b, c, d, e, f):
        global GLOBAL_A
        GLOBAL_A = a
        f = ""
        l = len(a)
        i = 0
        results = ""
        while i < l:
            s = ""
            while a[i] != c[e]:
                s += a[i]
                i += 1
            b = ""
            for j in range(len(s)):
                k = c.index(s[j])
                b += str(k)
            i += 1
            results = results + chr(self.dl25c(b, e, 10) - d)
        search_string = re.compile('(?:.+\')([^\']+)\';')
        m = re.search(search_string, results)
        if not m:
            # unable to obtain the url, abort
            self.logger.notice('{}: Unable to find m3u8 string, aborting. {}'
                               .format(self.plugin_obj.name, results))
            return

        enc_stream_url = m[1]
        stream_url = base64.b64decode(enc_stream_url)
        return stream_url

        
    def dl25c(self, a, b, c):
        global GLOBAL_A
        g = self.plugin_obj.unc_daddylive_dl22e
        s = slice(0, b)
        h = g[s]
        s = slice(0, c)
        i = g[s]
        j = a[::-1]
        w = 0
        total = 0
        for x in j:
            total = self.p(total, x, w, b, h)
            w += 1
        j = total
        k = ""
        while j > 0:
            k = i[int(j % c)] + k
            j = (j - (j % c)) / c
        if k == '':
            k = '0'
            self.logger.warning('###### K is blank and should not happen')
        return int(k)
    
    def p(self, total, value, index, b, h):
        global GLOBAL_A
        z = h.find(value)
        if z != -1:
            total += z * pow(b, index)
        return total
    
