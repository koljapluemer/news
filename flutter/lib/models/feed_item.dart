import 'dart:convert';

/// One entry from the pipeline's `data/feed.jsonl` (see
/// `docs/data_layout.md` in the repo root and `news.models.FeedEntry` on the
/// Python side, which this mirrors field-for-field).
class FeedItem {
  const FeedItem({
    required this.id,
    required this.source,
    required this.title,
    this.url,
    this.domain,
    required this.discussionUrl,
    this.points = 0,
    this.numComments = 0,
    required this.createdAt,
    this.finalScore,
    required this.surfacedAt,
    this.checkedOff = false,
    this.thumbsDown = false,
  });

  final String id;
  final String source;
  final String title;
  final String? url;
  final String? domain;
  final String discussionUrl;
  final int points;
  final int numComments;
  final DateTime createdAt;
  final double? finalScore;

  /// When a pipeline run last included this item in its top-N output. Feed
  /// entries are sorted by this, newest first.
  final DateTime surfacedAt;

  /// Set when the user dismisses the card; hidden from the feed view once
  /// true. Mirrors `checked_off` in `feed.jsonl`.
  final bool checkedOff;

  /// Like [checkedOff] (also hides the item), but records that the user
  /// actively disliked it, for a future pipeline stage to use as a negative
  /// signal. Mirrors `thumbs_down` in `feed.jsonl`.
  final bool thumbsDown;

  FeedItem copyWith({bool? checkedOff, bool? thumbsDown}) => FeedItem(
        id: id,
        source: source,
        title: title,
        url: url,
        domain: domain,
        discussionUrl: discussionUrl,
        points: points,
        numComments: numComments,
        createdAt: createdAt,
        finalScore: finalScore,
        surfacedAt: surfacedAt,
        checkedOff: checkedOff ?? this.checkedOff,
        thumbsDown: thumbsDown ?? this.thumbsDown,
      );

  /// The link a tap on the card should open: the external article URL if
  /// there is one (e.g. a Show HN with no link, or a self-post), otherwise
  /// the source's own discussion thread.
  String get primaryUrl => (url != null && url!.isNotEmpty) ? url! : discussionUrl;

  /// Parses one line of `feed.jsonl`. Returns null for a blank, malformed,
  /// or JSON-but-not-an-object line, and for one missing a required field --
  /// callers skip nulls rather than fail the whole file over one bad line.
  static FeedItem? fromJsonLine(String line) {
    if (line.trim().isEmpty) return null;
    try {
      final decoded = jsonDecode(line);
      if (decoded is! Map) return null;
      return fromJson(Map<String, dynamic>.from(decoded));
    } catch (_) {
      return null;
    }
  }

  static FeedItem? fromJson(Map<String, dynamic> json) {
    final id = json['id'];
    final source = json['source'];
    final title = json['title'];
    final discussionUrl = json['discussion_url'];
    final createdAt = _parseDate(json['created_at']);
    final surfacedAt = _parseDate(json['surfaced_at']);
    if (id is! String ||
        source is! String ||
        title is! String ||
        discussionUrl is! String ||
        createdAt == null ||
        surfacedAt == null) {
      return null;
    }

    final points = json['points'];
    final numComments = json['num_comments'];
    final finalScore = json['final_score'];
    return FeedItem(
      id: id,
      source: source,
      title: title,
      url: json['url'] is String ? json['url'] as String : null,
      domain: json['domain'] is String ? json['domain'] as String : null,
      discussionUrl: discussionUrl,
      points: points is int ? points : 0,
      numComments: numComments is int ? numComments : 0,
      createdAt: createdAt,
      finalScore: finalScore is num ? finalScore.toDouble() : null,
      surfacedAt: surfacedAt,
      checkedOff: json['checked_off'] == true,
      thumbsDown: json['thumbs_down'] == true,
    );
  }

  static DateTime? _parseDate(Object? raw) =>
      raw is String ? DateTime.tryParse(raw) : null;
}
