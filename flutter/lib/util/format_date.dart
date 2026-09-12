const _months = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

/// Formats [utc] (assumed UTC, as every timestamp in `feed.jsonl` is) in the
/// viewer's local time, e.g. "Sep 11, 14:30". No `intl` dependency needed for
/// a single fixed format.
String formatLocalDate(DateTime utc) {
  final local = utc.toLocal();
  final month = _months[local.month - 1];
  final minute = local.minute.toString().padLeft(2, '0');
  return '$month ${local.day}, ${local.hour}:$minute';
}
