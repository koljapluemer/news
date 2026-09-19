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

  Future<void> _pickFolder() async {
    setState(() => _busy = true);
    try {
      final path = await FilePicker.platform.getDirectoryPath(
        dialogTitle: 'Choose the pipeline data folder',
      );
      if (path != null) {
        await widget.repository.setDataDir(path);
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
        Text('Data folder', style: theme.textTheme.titleMedium),
        const SizedBox(height: 8),
        Text(repo.dataDir ?? 'Not set'),
        const SizedBox(height: 4),
        Text(
          'The pipeline\'s data/ folder. Each profile\'s feed is read from '
          'profiles/<name>/feed.jsonl inside it.',
          style: theme.textTheme.bodySmall,
        ),
        if (repo.dataDir != null) ...[
          const SizedBox(height: 8),
          Text(
            repo.profiles.isEmpty
                ? 'No profiles found'
                : 'Profiles: ${repo.profiles.join(', ')}',
          ),
          const SizedBox(height: 4),
          Text(
            repo.isLoading
                ? 'Loading feed…'
                : '${repo.count} items loaded'
                      '${repo.selectedProfile == null ? '' : ' from ${repo.selectedProfile}'}',
            style: theme.textTheme.bodySmall,
          ),
        ],
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
              onPressed: _busy ? null : _pickFolder,
              child: Text(_busy ? 'Loading…' : 'Choose data folder'),
            ),
            const SizedBox(width: 12),
            if (repo.dataDir != null)
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
            'Full disk access is required to read the data folder wherever '
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
