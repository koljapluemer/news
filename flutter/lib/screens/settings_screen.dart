import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:permission_handler/permission_handler.dart';

import '../repository/feed_repository.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key, required this.repository});

  final FeedRepository repository;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  bool _busy = false;

  Future<void> _pickFile() async {
    setState(() => _busy = true);
    try {
      final result = await FilePicker.platform.pickFiles(
        type: FileType.custom,
        allowedExtensions: ['jsonl'],
        dialogTitle: 'Choose feed.jsonl',
      );
      final path = result?.files.single.path;
      if (path != null) {
        await widget.repository.setPath(path);
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _requestPermission() async {
    await Permission.manageExternalStorage.request();
    setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final repo = widget.repository;
    final theme = Theme.of(context);
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Text('Feed file', style: theme.textTheme.titleMedium),
        const SizedBox(height: 8),
        Text(repo.feedPath ?? 'Not set'),
        const SizedBox(height: 4),
        Text(
          repo.isLoading ? 'Loading feed…' : '${repo.count} items loaded',
          style: theme.textTheme.bodySmall,
        ),
        if (repo.loadError != null) ...[
          const SizedBox(height: 8),
          Text(
            repo.loadError!,
            style: TextStyle(color: theme.colorScheme.error),
          ),
        ],
        const SizedBox(height: 16),
        Row(
          children: [
            FilledButton(
              onPressed: _busy ? null : _pickFile,
              child: Text(_busy ? 'Loading…' : 'Choose feed.jsonl'),
            ),
            const SizedBox(width: 12),
            if (repo.feedPath != null)
              OutlinedButton(
                onPressed: repo.isLoading ? null : repo.loadFromDisk,
                child: const Text('Refresh'),
              ),
          ],
        ),
        if (Platform.isAndroid) ...[
          const SizedBox(height: 32),
          Text('Storage permission', style: theme.textTheme.titleMedium),
          const SizedBox(height: 8),
          const Text(
            'Full disk access is required to read the feed file wherever '
            'it lives on the device.',
          ),
          const SizedBox(height: 8),
          OutlinedButton(
            onPressed: _requestPermission,
            child: const Text('Grant full disk access'),
          ),
        ],
      ],
    );
  }
}
