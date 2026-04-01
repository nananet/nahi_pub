# 日記フォルダ

## コミット履歴をその日の日記に自動追記する

リポジトリ直下で次を一度だけ実行する。

```bash
git config core.hooksPath .githooks
```

以降、通常の `git commit` のたびに **コミットの author date** に対応する `01_diary/YYYY/YYYY-MM-DD.md` の `## Timeline` に、

`- HH:MM \`短SHA\` コミット件名`

が追記される。

- 件名が `diary:` で始まるコミットでは追記しない（自動ログの再帰防止）。
- 無効にしたいときは `SKIP_DIARY_LOG=1 git commit ...`。
- **追記だけ**がデフォルト。追記後に日記だけ自動コミットまでしたい場合は、`.githooks/post-commit` の先頭に `export DIARY_AUTO_COMMIT=1` を足すか、環境で常に `DIARY_AUTO_COMMIT=1` を付ける。

Windows では Git for Windows の Bash がフックを実行する。`python` が PATH にある必要がある。
