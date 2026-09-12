import 'package:flutter/material.dart';

import '../repository/feed_repository.dart';
import '../widgets/feed_card.dart';

const _cardCount = 10;

class FeedScreen extends StatelessWidget {
  const FeedScreen({super.key, required this.repository});

  final FeedRepository repository;

  @override
  Widget build(BuildContext context) {
    if (repository.feedPath == null) {
      return const Center(
        child: Padding(
          padding: EdgeInsets.all(24),
          child: Text(
            'No feed file chosen yet. Pick feed.jsonl in Settings.',
            textAlign: TextAlign.center,
          ),
        ),
      );
    }

    final items = repository.latest(_cardCount);

    if (repository.isLoading && items.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }

    if (repository.loadError != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Text(
            'Could not read feed:\n${repository.loadError}',
            textAlign: TextAlign.center,
            style: TextStyle(color: Theme.of(context).colorScheme.error),
          ),
        ),
      );
    }

    if (items.isEmpty) {
      return const Center(child: Text('No items in the feed yet.'));
    }

    return RefreshIndicator(
      onRefresh: repository.loadFromDisk,
      child: ListView.builder(
        padding: const EdgeInsets.symmetric(vertical: 8),
        itemCount: items.length,
        itemBuilder: (context, index) => FeedCard(item: items[index]),
      ),
    );
  }
}
