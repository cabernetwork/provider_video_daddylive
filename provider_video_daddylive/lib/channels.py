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
import logging
import os
import re
import threading
import time
import urllib.parse
from threading import Thread

from lib.plugins.plugin_channels import PluginChannels
from lib.common.decorators import handle_json_except
from lib.common.decorators import handle_url_except
import lib.common.utils as utils
from ..lib import daddylive
from .. import resources

TERMINATE_REQUESTED = False


class Channels(PluginChannels):

    def __init__(self, _instance_obj):
        super().__init__(_instance_obj)

        self.pnum = self.config_obj.data[self.config_section]['player-number']
        self.search_url = None
        self.search_m3u8 = None
        self.search_ch = re.compile(
            b'<a\s+class="card"[^>]*?href="([^"]+)"[^>]*?data-title="[^"]*"[^>]*>'
            b'.*?<div\s+class="card__title">\s*(.*?)\s*<\/div>.*?ID:\s*(\d+)\s*<\/div>'
            b'.*?<\/a>',
            re.IGNORECASE | re.DOTALL
        )
        self.groups_other = None
        self.ch_db_list = None

    def get_channels(self):
        self.ch_db_list = self.db.get_channels(self.plugin_obj.name, self.instance_key)

        ch_list = self.get_channel_list()
        if ch_list is None or len(ch_list) == 0:
            self.logger.notice('DaddyLive channel list is empty from provider, not updating Cabernet')
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
            'watch.php?id={}'.format(_channel_id)
        header = {'User-agent': self.plugin_obj.user_agent,
                  'Referer': url,
                  }

        text = self.get_uri_data(url, 2, header)
        if not text:
            self.logger.info('{}: 1 Unable to obtain url for channel {}, aborting'
                             .format(self.plugin_obj.name, _channel_id))
            return

        # this text contains the Player info
        player_ch = re.compile(b'\\">Player ([0-9])</button>')
        m=re.findall(player_ch, text)
        if m:
            self.groups_other = ['']
            for p in m:
                self.groups_other[0] += 'P' + p.decode('utf-8')
        else:
            self.groups_other = None

        url2 = self.plugin_obj.unc_daddylive_base + \
            self.plugin_obj.unc_daddylive_stream[self.pnum].format(_channel_id)
        header = {'User-agent': self.plugin_obj.user_agent,
                  'Referer': url
                  }

        text2 = self.get_uri_data(url2, 2, header)
        data = self.db.get_channel(_channel_id, self.plugin_obj.name, self.instance_key)
        if data:
            ch_json = data['json']
        else:
            ch_json = None

        url3_match = re.search(b'iframe src="([^"]*)', text2)
        if not url3_match:
            self.logger.notice('Player {} not available for {}'.format(self.pnum, _channel_id))
            return

        # url3 is the reference urls with full string...
        url3_match = re.search(b'iframe src="([^"]*)', text2)
        if not url3_match:
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

        ch_url = url3_match.group(1).decode('utf8')
        header = {'User-agent': self.plugin_obj.user_agent,
                  'Referer': self.plugin_obj.unc_daddylive_base,
                  'Connection' : 'Keep-Alive'
                  }

        text = self.get_uri_data(ch_url, 2, header)
        json_updated = self.update_header(ch_json, ch_url)
        if "top2new" in ch_url:
            self.logger.warning('Bad URL from DL {}'.format(ch_url))

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
                    self.logger.notice('{}: Player changed for channel {} ref {}.  Data has been reset'.format(self.plugin_obj.name, _ch_json['id'], ref_url))
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
            'User-agent': self.plugin_obj.user_agent,
            'Referer': self.plugin_obj.unc_daddylive_base,
            'accept-language': 'en-US,en;q=0.5',
            'cookie': 'access=true'
            }
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
        match_list = re.findall(self.search_ch, text)

        # url, id, name
        for m in match_list:
            self.groups_other = None
            if len(m) != 3:
                self.logger.warning(
                    'get_channel_list - DaddyLive channel extraction failed. Extraction procedure needs updating')
                return None
            uid = m[2].decode('utf-8')
            name = html.unescape(m[1].decode('utf-8')).strip()
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
                        ref_url_full = self.get_channel_ref(uid)
                        if not ref_url_full:
                            self.logger.notice('{} BAD CHANNEL found, disabling {}:{}'
                                               .format(self.plugin_obj.name, uid, name))
                            header = None
                            if self.config_obj.data[self.config_section]['player-disable_bad_channels']:
                                ch['enabled'] = 0
                        else:
                            parsed_url = urllib.parse.urlsplit(ref_url_full)
                            origin_url = parsed_url.scheme + '://' + parsed_url.netloc
                            ref_url = origin_url + '/'
                            header = {'User-agent': self.plugin_obj.user_agent,
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
                    ref_url_full = self.get_channel_ref(uid)
                    if ref_url_full:
                        parsed_url = urllib.parse.urlsplit(ref_url_full)
                        ref_url = parsed_url.scheme + '://' + parsed_url.netloc + '/'
                        self.logger.info('{}:{} 2a Updating Channel {}:{} {}'
                            .format(self.plugin_obj.name, self.instance_key, uid, name, ref_url))
                    else:
                        self.logger.notice('{} BAD CHANNEL found, disabling {}:{}'
                                           .format(self.plugin_obj.name, uid, name))
                        header = None
                        if self.config_obj.data[self.config_section]['player-disable_bad_channels']:
                            enabled = 0
            else:
                self.logger.info('{}:{} 2 New Channel Added {}:{}'
                    .format(self.plugin_obj.name, self.instance_key, uid, name))
                display_name = name
                if ch:
                    enabled = ch['enabled']
                else:
                    enabled = 1
                hd = 0
                thumb = None
                thumb_size = None

                ref_url_full = self.get_channel_ref(uid)
                if ref_url_full:
                        parsed_url = urllib.parse.urlsplit(ref_url_full)
                        ref_url = parsed_url.scheme + '://' + parsed_url.netloc + '/'
                        self.logger.info('{}:{} 2c Updating Channel {}:{} {}'
                            .format(self.plugin_obj.name, self.instance_key, uid, name, ref_url))
                        channel_ref = { self.pnum: ref_url_full }
                else:
                    self.logger.notice('{} BAD CHANNEL found, disabling {}:{}'
                                       .format(self.plugin_obj.name, uid, name))
                    header = None
                    if self.config_obj.data[self.config_section]['player-disable_bad_channels']:
                        enabled = 0

            data = self.db.get_channel(uid, self.plugin_obj.name, self.instance_key)
            if data:
                ch_json = data['json']
                channel_ref = ch_json.get('channel_ref')

            if not ref_url:
                header = None
            else:
                header = {'User-agent': self.plugin_obj.user_agent,
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

        c_key = re.search(b'(?s)const\\s+CHANNEL_KEY\\s*=\\s*\\"([^"]+)\\"', _text)
        if not c_key:
            # unable to obtain the url, abort
            self.logger.notice('{}: #2 Unable to obtain m3u8, possible provider updated website for channel {}, aborting'
                               .format(self.plugin_obj.name, _channel_id))
            return
        c_key = c_key[1].decode('utf8')

        auth_info = get_auth_info(_text)
        if not auth_info:
            self.logger.notice('Unable to obtain authorization keys, skipping')
            return

        # auth2, then server lookup
        parsed_url = urllib.parse.urlsplit(_ch_url)
        ref_url = parsed_url.scheme + '://' + parsed_url.netloc + '/'
        #a2_header = {
        #    'User-agent': self.plugin_obj.user_agent,
        #    'Referer': ref_url,
        #    'Origin': ref_url[:-1],
        #    'Accept': '*/*',
        #    'Accept-Language': 'en-US,en;q=0.5',
        #    'Sec-Fetch-Site': 'cross-site',
        #    'Sec-Fetch-Mode': 'cors',
        #    'Sec-Fetch-Dest': 'empty'
        #    }
        #a2_url = auth_info['auth2']['a2_url']
        #a2_data = auth_info['auth2']['data']
        #auth2 = self.get_uri_data(a2_url, 2, a2_header, _data=a2_data)
        #self.logger.trace('AUTHURL2= {}  DATA2= {}  HEADER2= {}  RESULT2= {}'.format(a2_url, a2_data, a2_header, auth2))

        key_url = '{}{}' \
            .format(
                #urllib.parse.urlparse(_ch_url).netloc,
                auth_info['serverlookup']['qkey'], 
                auth_info['serverlookup']['chkey'])
        header2 = {
            'User-agent': self.plugin_obj.user_agent,
            'Referer': _ch_url
            }
        text = self.get_uri_data(key_url, 2, header2)
        self.logger.trace('KEYLOOKUP= {}  HEADER2= {}  RESULT2= {}'.format(key_url, header2, text))

        # this tell them that the play is not available
        if b'Not Found' in text:
            self.logger.debug('url results: {} {}'.format(key_url, text))
            self.logger.notice('Channel {} not availble for Player {}'.format(_channel_id, self.pnum))
        
        m = re.search(b':"([^"]*)', text)
        try:
            s_key = m[1].decode('utf8')
            #self.logger.trace('Server key= {}'.format(s_key))
        except TypeError:
            self.logger.warning('{}: Server key for uid {} is not available, aborting'.format(self.plugin_obj.name, _channel_id))
            return
        s_key = m[1].decode('utf8')

        #self.poller = AuthPolling(self, None, None, None, _ch_url)

        if s_key == "top1/cdn":
            m3u8_url = f"https://top1.newkso.ru/top1/cdn/{c_key}/mono.css"
        else:
            m3u8_url = 'https://{}new.kiko2.ru/{}/{}/mono.css'.format(s_key, s_key, c_key)
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
    


    def stream_terminated(self, sid):
        """
        This is called when the stream is terminated. Used to stop
        any polling processing that the plugin is doing during the
        stream.
        """
        global TERMINATE_REQUESTED
        TERMINATE_REQUESTED = True
        return None


class AuthPolling(Thread):
    def __init__(self, _ch_class, _uri, _header, _data, _ch_url):
        global TERMINATE_REQUESTED
        Thread.__init__(self)
        self.ch_url = _ch_url
        self.uri = _uri
        self.data = _data
        self.header = _header
        self.channel_class_object = _ch_class
        self.logger = logging.getLogger(__name__ + str(threading.get_ident()))
        if not TERMINATE_REQUESTED:
            self.start()

    def run(self):
        global TERMINATE_REQUESTED
        self.logger.trace('AuthPolling started {} {} {}'.format(self.uri, os.getpid(), threading.get_ident()))
        parsed_url = urllib.parse.urlsplit(self.ch_url)
        ref_url = parsed_url.scheme + '://' + parsed_url.netloc + '/'

        refresh = 600 # every 10 minutes
        while not TERMINATE_REQUESTED:
            count = 120
            while count > 0:
                refresh -= 2
                count -= 2
                time.sleep(2.0)
                if TERMINATE_REQUESTED:
                    break
            if TERMINATE_REQUESTED:
                break
            if refresh < 1:
                self.logger.trace('{} Refreshing auth2 data'.format(self.channel_class_object.plugin_obj.name))
                # need to requery and refresh auth and auth2
                refresh = 600 # every 10 minutes
                a_header = {
                    'User-agent': self.channel_class_object.plugin_obj.user_agent,
                    'Referer': self.ch_url}
                text = self.channel_class_object.get_uri_data(self.ch_url, 2, _header=a_header)
                #if not text:
                #    self.logger.notice('#2 {} Unable to refresh auth data, continuing'
                #                       .format(self.channel_class_object.plugin_obj.name))
                #else:
                #    auth_info = get_auth_info(text)
                #    if not auth_info:
                #        self.logger.notice('#3 {} Unable to refresh auth data, continuing'
                #                           .format(self.channel_class_object.plugin_obj.name))
                #    else:
                #        # update the auth2 data locally
                #        self.uri = auth_info['auth2']['a2_url']
                #        self.data = auth_info['auth2']['data']
 
            #self.channel_class_object.config_obj.refresh_config_data()

            #auth2 = self.channel_class_object.get_uri_data(self.uri, 2, self.header, _data=self.data)
            #self.logger.trace('#2 AUTHURL2= {}  DATA2= {}  HEADER2= {}  RESULT2= {}' \
            #    .format(self.uri, self.data, self.header, auth2))

        self.logger.trace('AuthPolling terminated {} {}'.format(os.getpid(), threading.get_ident()))
        self.uri = None
        self.data = None
        self.header = None


def get_auth_info(_text):

    logger = logging.getLogger(__name__ + str(threading.get_ident()))

    c_key = re.search(b'(?s)const\\s+CHANNEL_KEY\\s*=\\s*\\"([^"]+)\\"', _text)
    if not c_key:
        # unable to obtain the url, abort
        logger.notice('{}: #3 Unable to obtain m3u8, possible provider updated website, aborting'
                           .format('DaddyLive'))
        return
    #logger.warning(c_key)

    auth_str = re.search(b'(?s)use strict\'\;\\s+const var_[^"]+\\"([^"]+)\\"', _text)
    #logger.warning(auth_str)
    auth_str = auth_str[1].decode('utf8')
    #logger.warning(auth_str)

    #logger.warning('##########################################################################')
    #logger.warning(_text)
    #logger.warning('##########################################################################')
    

    c_key = c_key[1].decode('utf8')
    #logger.warning(c_key)
    #/server_lookup.php?channel_id=
    key_q = re.search(b'fetchWithRetry\\(\\s*\'([^\']*)', _text)
    #logger.warning(key_q)
    key_q = key_q[1].decode('utf-8')
    #logger.warning(key_q)

    #a2_url = re.search(b'fetchWithRetry\\(\\s*\'(http[^\']*)', _text)[1].decode('utf-8')
    ##https://auth.giokko.ru/auth2.php

    #a2_c_key = c_key

    #a2_country = re.search(b'(?s)const\\s+AUTH_COUNTRY\\s*=\\s*\\"([^"]+)\\"', _text)
    #a2_country = a2_country[1].decode('utf8')

    #a2_timestamp = re.search(b'(?s)const\\s+AUTH_TS\\s*=\\s*\\"([^"]+)\\"', _text)
    #a2_timestamp = a2_timestamp[1].decode('utf8')

    #a2_expiry = re.search(b'(?s)const\\s+AUTH_EXPIRY\\s*=\\s*\\"([^"]+)\\"', _text)
    #a2_expiry = a2_expiry[1].decode('utf8')

    #a2_token = re.search(b'(?s)const\\s+AUTH_TOKEN\\s*=\\s*\\"([^"]+)\\"', _text)
    #a2_token = a2_token[1].decode('utf8')

    #try:
    #    data = { "channelKey": a2_c_key,
    #             "country": a2_country,
    #             "timestamp": a2_timestamp,
    #             "expiry": a2_expiry,
    #             "token": a2_token
    #           }
    #except TypeError as ex:
    #    # Unable to obtain auth keys, skipping
    #    raise ex
    #    return
    results = { "serverlookup": { "chkey": c_key, "qkey": key_q },
        #"auth2": { "data": data, "a2_url": a2_url }
        "auth": { "auth_str": auth_str }
        }
    logger.trace('get_auth_info = {}'.format(results))

    return results