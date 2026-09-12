import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../models/feed_item.dart';

const _prefsPathKey = 'feed_path';

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

class FeedRepository extends ChangeNotifier {
  String? feedPath;
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

  /// Restores the persisted feed file path only. This is fast and must
  /// finish before the first frame; the actual parse is left to a separate,
  /// caller-triggered [loadFromDisk].
  Future<void> init() async {
    final prefs = await SharedPreferences.getInstance();
    feedPath = prefs.getString(_prefsPathKey);
  }

  Future<void> setPath(String path) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_prefsPathKey, path);
    feedPath = path;
    await loadFromDisk();
  }

  Future<void> loadFromDisk() async {
    final path = feedPath;
    if (path == null) return;
    isLoading = true;
    loadError = null;
    notifyListeners();

    try {
      final loaded = await compute(parseFeedIsolate, path);
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
}
