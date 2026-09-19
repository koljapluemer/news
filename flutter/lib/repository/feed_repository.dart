import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:path/path.dart' as p;
import 'package:shared_preferences/shared_preferences.dart';

import '../models/feed_item.dart';

const _prefsDataDirKey = 'data_dir';
const _prefsProfileKey = 'profile';

/// How many not-checked-off items the feed screen should try to show.
const visibleFeedTotal = 10;

/// Of [visibleFeedTotal], how many are filled with the most-recently
/// surfaced items -- the rest come from the oldest end of the backlog so it
/// eventually clears instead of never being seen.
const visibleFeedNewestCount = 7;

/// Reads and parses every line of the `feed.jsonl` at [path]. Runs in a
/// background isolate via [compute] so a large, ever-growing history file
/// never blocks the UI thread. Lines that don't parse are skipped, never
/// fatal -- see [FeedItem.fromJsonLine].
List<FeedItem> parseFeedIsolate(String path) {
  final file = File(path);
  if (!file.existsSync()) return [];
  final items = <FeedItem>[];
  for (final line in file.readAsLinesSync()) {
    final item = FeedItem.fromJsonLine(line);
    if (item != null) items.add(item);
  }
  return items;
}

/// Names of the profiles under `<dataDir>/profiles/` -- the sub-directories
/// that already contain a `feed.jsonl` (i.e. the pipeline has run for them
/// at least once). Sorted alphabetically.
List<String> listProfiles(String dataDir) {
  final dir = Directory(p.join(dataDir, 'profiles'));
  if (!dir.existsSync()) return [];
  final names = <String>[];
  for (final entry in dir.listSync()) {
    if (entry is Directory &&
        File(p.join(entry.path, 'feed.jsonl')).existsSync()) {
      names.add(p.basename(entry.path));
    }
  }
  names.sort();
  return names;
}

class FeedRepository extends ChangeNotifier {
  /// The pipeline's data directory (the one holding `raw/` and `profiles/`).
  String? dataDir;

  /// Profiles found under [dataDir], see [listProfiles].
  List<String> profiles = [];

  /// The profile whose feed is shown; one of [profiles], or null if none.
  String? selectedProfile;

  bool isLoading = false;
  String? loadError;
  DateTime? lastLoadedAt;

  List<FeedItem> _items = [];

  /// All parsed items, sorted by [FeedItem.surfacedAt] descending (newest
  /// surfaced first).
  List<FeedItem> get items => List.unmodifiable(_items);

  int get count => _items.length;

  /// The [n] most recently surfaced items, newest first.
  List<FeedItem> latest(int n) => _items.take(n).toList();

  /// Items to show on the feed screen: not-checked-off items, up to
  /// [total] of them -- the [newestCount] most-recently-surfaced first,
  /// then the oldest remaining ones, so the backlog of older items
  /// eventually gets seen instead of being buried forever by new arrivals.
  List<FeedItem> visibleItems({
    int total = visibleFeedTotal,
    int newestCount = visibleFeedNewestCount,
  }) {
    // _items is sorted by surfacedAt descending, so pending is too.
    final pending = _items.where((i) => !i.checkedOff).toList();
    if (pending.length <= total) return pending;
    final oldestNeeded = total - newestCount;
    return [
      ...pending.take(newestCount),
      ...pending.skip(pending.length - oldestNeeded),
    ];
  }

  /// Path of the selected profile's `feed.jsonl`, or null until both a data
  /// directory and a profile are known.
  String? get feedPath {
    final dir = dataDir;
    final profile = selectedProfile;
    if (dir == null || profile == null) return null;
    return p.join(dir, 'profiles', profile, 'feed.jsonl');
  }

  /// Restores the persisted data directory and profile choice, and lists
  /// the available profiles. This is fast and must finish before the first
  /// frame; the actual parse is left to a separate, caller-triggered
  /// [loadFromDisk].
  Future<void> init() async {
    final prefs = await SharedPreferences.getInstance();
    dataDir = prefs.getString(_prefsDataDirKey);
    _refreshProfiles(prefs.getString(_prefsProfileKey));
  }

  Future<void> setDataDir(String path) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_prefsDataDirKey, path);
    dataDir = path;
    _refreshProfiles(selectedProfile);
    await loadFromDisk();
  }

  Future<void> selectProfile(String name) async {
    if (name == selectedProfile) return;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_prefsProfileKey, name);
    selectedProfile = name;
    _items = [];
    await loadFromDisk();
  }

  /// Re-scans [dataDir] for profiles. Keeps [preferred] selected if it still
  /// exists, otherwise falls back to the first profile (or null if none).
  void _refreshProfiles(String? preferred) {
    final dir = dataDir;
    profiles = dir == null ? [] : listProfiles(dir);
    selectedProfile = profiles.contains(preferred)
        ? preferred
        : (profiles.isEmpty ? null : profiles.first);
  }

  Future<void> loadFromDisk() async {
    // A pipeline run may have created a new profile since the last scan.
    _refreshProfiles(selectedProfile);
    final path = feedPath;
    if (path == null) {
      _items = [];
      loadError = dataDir == null
          ? null
          : 'No profiles found in ${p.join(dataDir!, 'profiles')}. '
                'Run the pipeline first.';
      notifyListeners();
      return;
    }
    isLoading = true;
    loadError = null;
    notifyListeners();

    try {
      final loaded = await compute(parseFeedIsolate, path);
      // The user switched profile while this parse was running.
      if (path != feedPath) return;
      loaded.sort((a, b) => b.surfacedAt.compareTo(a.surfacedAt));
      _items = loaded;
      lastLoadedAt = DateTime.now();
    } catch (e) {
      loadError = e.toString();
      _items = [];
    }

    isLoading = false;
    notifyListeners();
  }

  /// Marks [id] as checked off (dismissed/read), hiding it from the feed.
  Future<void> checkOff(String id) => _patchEntry(id, {'checked_off': true});

  /// Marks [id] as thumbs-downed -- hides it like [checkOff], but also
  /// records the negative signal separately for a future pipeline stage.
  Future<void> markThumbsDown(String id) =>
      _patchEntry(id, {'checked_off': true, 'thumbs_down': true});

  /// Patches [patch] into the JSON object on [id]'s line in `feed.jsonl`,
  /// rewriting the file via temp file + rename (same convention as
  /// `storage.upsert_feed` on the Python side) so a concurrent reader never
  /// sees a half-written line. Every other line is left byte-for-byte
  /// untouched. Updates the in-memory item in place rather than re-parsing
  /// the whole file.
  Future<void> _patchEntry(String id, Map<String, dynamic> patch) async {
    final path = feedPath;
    if (path == null) return;
    final file = File(path);
    if (!file.existsSync()) return;

    final lines = await file.readAsLines();
    var found = false;
    final updated = <String>[];
    for (final line in lines) {
      if (found || line.trim().isEmpty) {
        updated.add(line);
        continue;
      }
      Map<String, dynamic>? decoded;
      try {
        final d = jsonDecode(line);
        if (d is Map) decoded = Map<String, dynamic>.from(d);
      } catch (_) {
        // Malformed line -- leave it as-is, same tolerance as FeedItem.fromJsonLine.
      }
      if (decoded != null && decoded['id'] == id) {
        decoded.addAll(patch);
        updated.add(jsonEncode(decoded));
        found = true;
      } else {
        updated.add(line);
      }
    }
    if (!found) return;

    final tmp = File('$path.tmp');
    await tmp.writeAsString('${updated.join('\n')}\n');
    await tmp.rename(path);

    final index = _items.indexWhere((i) => i.id == id);
    if (index != -1) {
      _items[index] = _items[index].copyWith(
        checkedOff: patch['checked_off'] as bool?,
        thumbsDown: patch['thumbs_down'] as bool?,
      );
      notifyListeners();
    }
  }
}
