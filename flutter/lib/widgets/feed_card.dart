import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../models/feed_item.dart';
import '../util/format_date.dart';

class FeedCard extends StatelessWidget {
  const FeedCard({
    super.key,
    required this.item,
    required this.onCheckOff,
    required this.onThumbsDown,
  });

  final FeedItem item;

  /// Called when the user dismisses the card as read.
  final VoidCallback onCheckOff;

  /// Called when the user marks the card as not interesting.
  final VoidCallback onThumbsDown;

  Future<void> _open(BuildContext context, String url) async {
    final uri = Uri.tryParse(url);
    if (uri == null) return;
    final ok = await launchUrl(uri, mode: LaunchMode.externalApplication);
    if (!ok && context.mounted) {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text('Could not open $url')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final hasSeparateDiscussion = item.primaryUrl != item.discussionUrl;

    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      child: InkWell(
        onTap: () => _open(context, item.primaryUrl),
        borderRadius: BorderRadius.circular(12),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: Text(item.title, style: theme.textTheme.titleMedium),
                  ),
                  IconButton(
                    icon: const Icon(Icons.check_circle_outline),
                    tooltip: 'Mark as read',
                    visualDensity: VisualDensity.compact,
                    onPressed: onCheckOff,
                  ),
                  IconButton(
                    icon: const Icon(Icons.thumb_down_outlined),
                    tooltip: 'Not interested',
                    visualDensity: VisualDensity.compact,
                    onPressed: onThumbsDown,
                  ),
                ],
              ),
              const SizedBox(height: 8),
              Row(
                children: [
                  Chip(
                    label: Text(item.source),
                    visualDensity: VisualDensity.compact,
                    materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                  ),
                  const SizedBox(width: 8),
                  Text(
                    formatLocalDate(item.createdAt),
                    style: theme.textTheme.bodySmall,
                  ),
                  if (item.domain != null) ...[
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        item.domain!,
                        style: theme.textTheme.bodySmall,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  ],
                ],
              ),
              if (hasSeparateDiscussion) ...[
                const SizedBox(height: 4),
                InkWell(
                  onTap: () => _open(context, item.discussionUrl),
                  child: Text(
                    'Discussion (${item.numComments})',
                    style: theme.textTheme.bodySmall
                        ?.copyWith(color: theme.colorScheme.primary),
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
