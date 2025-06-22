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

import random

import lib.common.exceptions as exceptions
from lib.plugins.plugin_obj import PluginObj

from .daddylive_instance import DaddyLiveInstance
from ..lib import translations
import lib.common.utils as utils

class DaddyLive(PluginObj):

    def __init__(self, _plugin, _plugins):
        super().__init__(_plugin)
        b = 'player-tuner_count'
        self.plugins = _plugins
        # create an instance is none are present
        self.enable_instance(self.namespace, None)
        count = self.config_obj.data[self.namespace.lower()][b]
        if count > 4:
            self.config_obj.data[self.namespace.lower()][b] = 1
        if self.config_obj.data[self.namespace.lower()]['epg-plugin'] == 'ALL':
            self.enable_instance('TVGuide', 'default')
        self.unc_daddylive_dl25f = [b"" for i in range(6)]
        self.unc_daddylive_dl25f[1] = self.uncompress(translations.daddylive_dl25f_stream).encode()
        self.unc_daddylive_dl25f[2] = self.uncompress(translations.daddylive_dl25f_cast).encode()
        self.unc_daddylive_dl25f[3] = self.uncompress(translations.daddylive_dl25f_watch).encode()
        self.unc_daddylive_dl25f[4] = self.uncompress(translations.daddylive_dl25f_player).encode()
        self.unc_daddylive_dl25f[5] = self.uncompress(translations.daddylive_dl25f_casting).encode()
        self.unc_daddylive_dl21a = [b"" for i in range(6)]
        self.unc_daddylive_dl21a[1] = self.uncompress(translations.daddylive_dl21a_stream).encode()
        self.unc_daddylive_dl21a[2] = self.uncompress(translations.daddylive_dl21a_cast).encode()
        self.unc_daddylive_dl21a[3] = self.uncompress(translations.daddylive_dl21a_watch).encode()
        self.unc_daddylive_dl21a[4] = self.uncompress(translations.daddylive_dl21a_player).encode()
        self.unc_daddylive_dl21a[5] = self.uncompress(translations.daddylive_dl21a_casting).encode()
        for inst in _plugin.instances:
            self.instances[inst] = DaddyLiveInstance(self, inst)
        self.unc_daddylive_base = self.uncompress(translations.daddylive_base) \
            .format(self.config_obj.data[self.namespace.lower()]['daddylive_host'])
        self.unc_daddylive_channels = self.uncompress(translations.daddylive_channels)
        self.unc_daddylive_stream = ["" for i in range(6)]
        self.unc_daddylive_stream[1] = self.uncompress(translations.daddylive_stream_stream)
        self.unc_daddylive_stream[2] = self.uncompress(translations.daddylive_stream_cast)
        self.unc_daddylive_stream[3] = self.uncompress(translations.daddylive_stream_watch)
        self.unc_daddylive_stream[4] = self.uncompress(translations.daddylive_stream_player)
        self.unc_daddylive_stream[5] = self.uncompress(translations.daddylive_stream_casting)
        self.unc_daddylive_dl22e = self.uncompress(translations.daddylive_dl22e)
        self.unc_daddylive_key_stream = self.uncompress(translations.daddylive_key_stream)
        self.unc_daddylive_key_url = self.uncompress(translations.daddylive_key_url)
        utils.DEFAULT_USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36'


    def scan_channels(self, _instance=None):
        """
        Called from the scheduler
        Requests a stream for each of the disabled channels to see if they
        are up
        """
        try:
            if not self.enabled:
                self.logger.debug('{} Plugin disabled, not scanning channels'
                                  .format(self.plugin.name))
                return
            if _instance is None:
                for key, instance in self.instances.items():
                    instance.scan_channels()
            else:
                self.instances[_instance].scan_channels()
        except exceptions.CabernetException:
            self.logger.debug('CabernetException channel scan task: Setting plugin {} to disabled'
                              .format(self.plugin.name))
            self.enabled = False
            self.plugin.enabled = False

    def scheduler_tasks(self):
        sched_epg_hours_1 = 4
        sched_epg_hours_2 = 18
        sched_epg_mins = random.randint(1, 55)
        sched_epg_1 = '{:0>2d}:{:0>2d}'.format(sched_epg_hours_1, sched_epg_mins)
        sched_epg_2 = '{:0>2d}:{:0>2d}'.format(sched_epg_hours_2, sched_epg_mins)
        sched_ch_hours = self.utc_to_local_time(23)
        sched_ch_mins = random.randint(1, 55)
        sched_ch = '{:0>2d}:{:0>2d}'.format(sched_ch_hours, sched_ch_mins)
                
        if self.scheduler_db.save_task(
                'Channels',
                'Refresh {} Channels'.format(self.namespace),
                self.name,
                None,
                'refresh_channels',
                20,
                'inline',
                'Pulls channel lineup from {}'.format(self.namespace)
        ):
            self.scheduler_db.save_trigger(
                'Channels',
                'Refresh {} Channels'.format(self.namespace),
                'startup')
            self.scheduler_db.save_trigger(
                'Channels',
                'Refresh {} Channels'.format(self.namespace),
                'daily',
                timeofday=sched_ch
            )
        # this runs slow and long, so make it the last one to run with lower priority
        if self.scheduler_db.save_task(
                'EPG',
                'Refresh {} EPG'.format(self.namespace),
                self.name,
                None,
                'refresh_epg',
                5,
                'thread',
                'Pulls channel program data from {}'.format(self.namespace)
        ):
            self.scheduler_db.save_trigger(
                'EPG',
                'Refresh {} EPG'.format(self.namespace),
                'startup')
            # update after midnight local time
            self.scheduler_db.save_trigger(
                'EPG',
                'Refresh {} EPG'.format(self.namespace),
                'daily',
                timeofday=sched_epg_1
            )
            self.scheduler_db.save_trigger(
                'EPG',
                'Refresh {} EPG'.format(self.namespace),
                'daily',
                timeofday=sched_epg_2
            )
