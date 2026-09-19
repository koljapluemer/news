import 'package:flutter/material.dart';

import '../repository/feed_repository.dart';
import 'feed_screen.dart';
import 'settings_screen.dart';

class HomeShell extends StatefulWidget {
  const HomeShell({super.key, required this.repository});

  final FeedRepository repository;

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _index = 0;

  @override
  Widget build(BuildContext context) {
    final pages = [
      FeedScreen(repository: widget.repository),
      SettingsScreen(repository: widget.repository),
    ];
    return Scaffold(
      appBar: AppBar(title: _ProfileSelector(repository: widget.repository)),
      body: SafeArea(
        child: IndexedStack(index: _index, children: pages),
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (i) => setState(() => _index = i),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.article), label: 'Feed'),
          NavigationDestination(icon: Icon(Icons.settings), label: 'Settings'),
        ],
      ),
    );
  }
}

/// App-bar dropdown for choosing which profile's news to show.
class _ProfileSelector extends StatelessWidget {
  const _ProfileSelector({required this.repository});

  final FeedRepository repository;

  @override
  Widget build(BuildContext context) {
    final profiles = repository.profiles;
    if (profiles.isEmpty) return const Text('News');
    return DropdownButtonHideUnderline(
      child: DropdownButton<String>(
        value: repository.selectedProfile,
        isDense: true,
        style: Theme.of(context).textTheme.titleLarge,
        items: [
          for (final name in profiles)
            DropdownMenuItem(value: name, child: Text(name)),
        ],
        onChanged: (name) {
          if (name != null) repository.selectProfile(name);
        },
      ),
    );
  }
}
