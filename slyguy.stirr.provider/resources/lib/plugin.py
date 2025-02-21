import codecs

import arrow
from slyguy import plugin, inputstream, mem_cache, settings, gui, userdata
from slyguy.session import Session

from .language import _
from .constants import *

@plugin.route('')
def home(**kwargs):
    folder = plugin.Folder()

    folder.add_item(label=_(_.LIVE_TV, _bold=True), path=plugin.url_for(live_tv))
    folder.add_item(label=_(_.MY_CHANNELS, _bold=True), path=plugin.url_for(live_tv, code=MY_CHANNELS))
    folder.add_item(label=_(_.SEARCH, _bold=True), path=plugin.url_for(search))

    if settings.getBool('bookmarks', True):
        folder.add_item(label=_(_.BOOKMARKS, _bold=True),  path=plugin.url_for(plugin.ROUTE_BOOKMARKS), bookmark=False)

    folder.add_item(label=_.SETTINGS, path=plugin.url_for(plugin.ROUTE_SETTINGS), _kiosk=False, bookmark=False)

    return folder

@plugin.route()
def add_favourite(id, **kwargs):
    data = _app_data()
    channel = data['regions'][ALL]['channels'].get(id)
    if not channel:
        return

    favourites = userdata.get('favourites') or []
    if id not in favourites:
        favourites.append(id)

    userdata.set('favourites', favourites)
    gui.notification(_.MY_CHANNEL_ADDED, heading=channel['name'], icon=channel['logo'])

@plugin.route()
def del_favourite(id, **kwargs):
    favourites = userdata.get('favourites') or []
    if id in favourites:
        favourites.remove(id)

    userdata.set('favourites', favourites)
    gui.refresh()

@mem_cache.cached(60*15)
def _data():
    return Session().gz_json(DATA_URL)

def _app_data():
    data = _data()

    favourites = userdata.get('favourites') or []
    my_channels = {'logo': None, 'name': _.MY_CHANNELS, 'channels': {}, 'sort': 0}
    all_channels = {'logo': None, 'name':_.ALL, 'channels': {}, 'sort': 1}
    for id in data['channels']:
        all_channels['channels'][id] = data['channels'][id]
        if id in favourites:
            my_channels['channels'][id] = data['channels'][id]

    data['regions'] = {
        ALL: all_channels,
        MY_CHANNELS: my_channels,
    }

    return data

def _process_channels(channels, group=ALL, region=ALL):
    items = []

    show_chno = settings.getBool('show_chno', True)

    if settings.getBool('show_epg', True):
        now = arrow.now()
        epg_count = 5
    else:
        epg_count = None

    for id in sorted(channels.keys(), key=lambda x: channels[x]['chno'] if show_chno else channels[x]['name'].strip().lower()):
        channel = channels[id]
        if group != ALL and group not in channel['groups']:
            continue

        plot = u'[B]{}[/B]\n'.format(' / '.join(channel['groups']))
        if not epg_count:
            plot += channel['description'] or ''
        else:
            count = 0
            for index, row in enumerate(channel.get('programs', [])):
                start = arrow.get(row[0])
                try: stop = arrow.get(channel['programs'][index+1][0])
                except: stop = start.shift(hours=1)

                if (now > start and now < stop) or start > now:
                    plot += u'[{}] {}\n'.format(start.to('local').format('h:mma'), row[1])
                    count += 1
                    if count == epg_count:
                        break

        item = plugin.Item(
            label = u'{} | {}'.format(channel['chno'], channel['name']) if show_chno else channel['name'],
            info = {'plot': plot},
            art = {'thumb': channel['logo']},
            playable = True,
            path = plugin.url_for(play, id=id, _is_live=True),
            context = ((_.DEL_MY_CHANNEL, 'RunPlugin({})'.format(plugin.url_for(del_favourite, id=id))),) if region == MY_CHANNELS else ((_.ADD_MY_CHANNEL, 'RunPlugin({})'.format(plugin.url_for(add_favourite, id=id))),),
        )
        items.append(item)

    return items

@plugin.route()
def live_tv(code=None, group=None, **kwargs):
    data = _app_data()

    if not settings.getBool('show_groups', True):
        group = ALL

    if code is None:
        code = ALL

    region = data['regions'][code]
    folder = plugin.Folder(region['name'])
    channels = region['channels']

    if group is None:
        groups = {}
        all_count = 0
        for id in channels:
            channel = channels[id]
            all_count += 1
            for group in channel['groups']:
                if group not in groups:
                    groups[group] = 1
                else:
                    groups[group] += 1

        folder = plugin.Folder(_.LIVE_TV)

        if all_count:
            folder.add_item(
                label = _(u'{name} ({count})'.format(name=_.ALL, count=all_count)),
                art = {'thumb': region.get('logo')},
                path = plugin.url_for(live_tv, code=code, group=ALL),
            )

        for group in sorted(groups):
            folder.add_item(
                label = _(u'{name} ({count})'.format(name=group, count=groups[group])),
                art = {'thumb': region.get('logo')},
                info = {
                    'plot': u'{}\n\n{}'.format(group, _(_.CHANNEL_COUNT, count=groups[group])),
                },
                path = plugin.url_for(live_tv, code=code, group=group)
            )

        return folder

    folder = plugin.Folder(region['name'] if group == ALL else group, no_items_method='list')
    items = _process_channels(channels, group=group, region=code)
    folder.add_items(items)
    return folder

@plugin.route()
@plugin.search()
def search(query, page, **kwargs):
    data = _app_data()

    results = {}
    for id in data['regions'][ALL]['channels']:
        channel = data['regions'][ALL]['channels'][id]
        search_t = '{} {} {}'.format(channel['name'], channel['chno'], ' '.join(channel['groups']))
        if query.lower() in search_t.lower():
            results[id] = channel

    return _process_channels(results), False

@plugin.route()
def play(id, **kwargs):
    data = _app_data()
    channel = data['regions'][ALL]['channels'][id]

    return plugin.Item(
        label = channel['name'],
        info = {'plot': channel['description']},
        art = {'thumb': channel['logo']},
        inputstream = inputstream.HLS(live=True),
        headers = data['headers'],
        path = channel['url'],
    )

@plugin.route()
@plugin.merge()
def playlist(output, **kwargs):
    data = _app_data()
    regions = userdata.get('merge_regions', [])
    regions = [x for x in regions if x in data['regions']]

    if not regions:
        regions = [ALL]

    with codecs.open(output, 'w', encoding='utf8') as f:
        f.write(u'#EXTM3U')

        added = []
        for code in regions:
            region = data['regions'][code]
            channels = region['channels']

            for id in sorted(channels.keys(), key=lambda x: channels[x]['chno']):
                if id in added:
                    continue

                added.append(id)
                channel = channels[id]

                f.write(u'\n#EXTINF:-1 tvg-id="{id}" tvg-chno="{chno}" tvg-name="{name}" tvg-logo="{logo}" group-title="{group}",{name}\n{url}'.format(
                    id=id, chno=channel['chno'], name=channel['name'], logo=channel['logo'], group=';'.join(channel['groups']), url=plugin.url_for(play, id=id, _is_live=True),
                ))

@plugin.route()
def configure_merge(**kwargs):
    data = _app_data()
    user_regions = userdata.get('merge_regions', [])
    avail_regions = sorted(data['regions'], key=lambda x: (data['regions'][x]['sort'], data['regions'][x]['name']))

    options = []
    preselect = []
    for index, code in enumerate(avail_regions):
        region = data['regions'][code]
        options.append(plugin.Item(label=region['name'], art={'thumb': region['logo']}))
        if code in user_regions:
            preselect.append(index)

    indexes = gui.select(heading=_.SELECT_REGIONS, options=options, multi=True, useDetails=False, preselect=preselect)
    if indexes is None:
        return

    user_regions = [avail_regions[i] for i in indexes]
    userdata.set('merge_regions', user_regions)
