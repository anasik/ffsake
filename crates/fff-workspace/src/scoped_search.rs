//! Bounded explicit-path content search independent of index discovery rules.
use crate::journal::Journal;
use crate::search::{MatchSet, SearchMode, SearchRange};
use crate::{FileSelector, PathSelector, Workspace};
use serde_json::json;
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io::Read;

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

pub(crate) fn applies(target: &FileSelector) -> bool {
    !target.include.is_empty()
        && target
            .include
            .iter()
            .all(|r| matches!(r, PathSelector::Exact(_)))
}

pub(crate) fn search(
    workspace: &Workspace,
    pattern: &str,
    mode: SearchMode,
    target: &FileSelector,
    limit: usize,
    offset: usize,
) -> Result<MatchSet> {
    if offset != 0
        || target.include.len() > 100
        || target.exclude.len() > 100
        || pattern.is_empty()
        || pattern.len() > 8192
    {
        return Err("explicit search requires offset=0, 1..=100 exact roots and a nonempty pattern <=8192 bytes".into());
    }
    let journal = Journal::for_workspace(workspace)?;
    // Root is a valid read-only scope, though never a mutation target.
    let resolve = |path: &str| {
        if path == "." || path == "./" {
            Ok(workspace.root().to_path_buf())
        } else {
            journal.resolve(path)
        }
    };
    let mut pending = Vec::new();
    for rule in &target.include {
        if let PathSelector::Exact(path) = rule {
            pending.push((resolve(path)?, 0));
        }
    }
    let mut seen = BTreeSet::new();
    let mut files = BTreeSet::new();
    let mut skipped = 0;
    while let Some((path, depth)) = pending.pop() {
        let relative = path.as_path();
        if relative
            .components()
            .any(|p| p.as_os_str() == ".git" || p.as_os_str() == ".ffsake")
        {
            skipped += 1;
            continue;
        }
        if !seen.insert(path.clone()) {
            continue;
        }
        if seen.len() > 2000 || depth > 32 {
            return Err("explicit search exceeds 2000 entries/depth 32; narrow the target".into());
        }
        let metadata = fs::symlink_metadata(&path)?;
        if metadata.file_type().is_symlink() {
            skipped += 1;
            continue;
        }
        if metadata.is_dir() {
            for child in fs::read_dir(&path)? {
                if pending.len() + seen.len() >= 2000 {
                    return Err("explicit search exceeds 2000 entries; narrow the target".into());
                }
                pending.push((child?.path(), depth + 1));
            }
        } else if metadata.is_file() {
            files.insert(path);
        } else {
            skipped += 1;
        }
    }
    // Exclusions still apply, even though hidden/ignore discovery rules do not.
    let mut excluded = BTreeSet::new();
    for rule in &target.exclude {
        if let PathSelector::Glob(pattern) | PathSelector::Regex(pattern) = rule
            && pattern.len() > 8192
        {
            return Err("explicit search exclusion exceeds 8192 bytes".into());
        }
        match rule {
            PathSelector::All => {
                excluded.extend(files.iter().cloned());
            }
            PathSelector::Exact(path) => {
                let path = resolve(path)?;
                excluded.extend(files.iter().filter(|p| p.starts_with(&path)).cloned());
            }
            PathSelector::Glob(pattern) => {
                let matcher = globset::Glob::new(pattern)?.compile_matcher();
                excluded.extend(
                    files
                        .iter()
                        .filter(|p| matcher.is_match(workspace.relative_display(p)))
                        .cloned(),
                );
            }
            PathSelector::Regex(pattern) => {
                let matcher = regex::RegexBuilder::new(pattern)
                    .size_limit(1024 * 1024)
                    .build()?;
                excluded.extend(
                    files
                        .iter()
                        .filter(|p| matcher.is_match(&workspace.relative_display(p)))
                        .cloned(),
                );
            }
        }
    }
    let expression = regex::RegexBuilder::new(&if mode == SearchMode::Literal {
        regex::escape(pattern)
    } else {
        pattern.into()
    })
    .size_limit(1024 * 1024)
    .build()?;
    let mut ranges = Vec::new();
    let mut hashes = BTreeMap::new();
    let mut total = 0usize;
    let mut binary = 0;
    let mut scanned = 0;
    for path in files.iter().filter(|p| !excluded.contains(*p)) {
        let name = workspace.relative_display(path);
        // Re-resolve every file before reading to retain ancestor/symlink guards.
        let checked = journal.resolve(&name)?;
        if !fs::symlink_metadata(&checked)?.is_file() {
            return Err("explicit search source changed type".into());
        }
        if fs::metadata(&checked)?.len() > 4 * 1024 * 1024 {
            return Err("explicit search file exceeds 4 MiB; narrow the target".into());
        }
        let mut bytes = Vec::new();
        fs::File::open(&checked)?
            .take(4 * 1024 * 1024 + 1)
            .read_to_end(&mut bytes)?;
        total += bytes.len();
        if bytes.len() > 4 * 1024 * 1024 || total > 16 * 1024 * 1024 {
            return Err("explicit search exceeds 4 MiB/file or 16 MiB total".into());
        }
        let Ok(text) = std::str::from_utf8(&bytes) else {
            binary += 1;
            continue;
        };
        if bytes.contains(&0) {
            binary += 1;
            continue;
        }
        scanned += 1;
        let mut base = 0;
        let before = ranges.len();
        for (line, text) in text.split_inclusive('\n').enumerate() {
            let text = text.strip_suffix('\n').unwrap_or(text);
            for found in expression.find_iter(text) {
                if ranges.len() >= limit {
                    return Err("explicit search match limit exceeded; narrow the query (no partial results)".into());
                }
                ranges.push(SearchRange {
                    path: name.clone(),
                    start_byte: base + found.start(),
                    end_byte: base + found.end(),
                    line: line as u64 + 1,
                });
            }
            base += text.len() + 1;
        }
        if ranges.len() != before {
            hashes.insert(name, blake3::hash(&bytes).to_hex().to_string());
        }
    }
    Ok(MatchSet {
        pattern: pattern.into(),
        mode,
        ranges,
        file_hashes: hashes,
        next_file_offset: 0,
        coverage: Some(
            json!({"backend":"explicit_paths","hidden_directories":"included","ignore_rules":"bypassed",
            "scanned_text_files":scanned,"binary_files_skipped":binary,"target_exclusions":excluded.len(),
            "protected_or_special_entries_skipped":skipped,"excluded_kinds":[".git",".ffsake","symlinks","special files","non-UTF-8 or NUL-containing files"],
            "limits":{"entries":2000,"depth":32,"file_bytes":4194304,"total_bytes":16777216},"complete_within_limits":true}),
        ),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn explicit_search_bounds_and_byte_offsets_remain_guarded() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join(".hidden")).unwrap();
        fs::write(root.path().join(".hidden/source"), "α needle\r\nneedle\n").unwrap();
        fs::write(root.path().join(".hidden/binary"), b"needle\0").unwrap();
        let workspace = Workspace::new(root.path()).unwrap();
        let target = FileSelector::exact(".hidden");
        assert!(search(&workspace, "needle", SearchMode::Literal, &target, 1, 0).is_err());
        let matches = search(&workspace, "needle", SearchMode::Literal, &target, 2, 0).unwrap();
        assert_eq!(
            matches
                .ranges
                .iter()
                .map(|r| (r.start_byte, r.end_byte, r.line))
                .collect::<Vec<_>>(),
            vec![(3, 9, 1), (11, 17, 2)]
        );
        assert_eq!(
            matches.coverage.as_ref().unwrap()["binary_files_skipped"],
            1
        );
        fs::write(root.path().join(".hidden/source"), "host change").unwrap();
        assert!(matches.validate(&workspace).is_err());
        let file = fs::File::create(root.path().join(".hidden/oversized")).unwrap();
        file.set_len(4 * 1024 * 1024 + 1).unwrap();
        assert!(
            search(&workspace, "needle", SearchMode::Literal, &target, 100, 0)
                .unwrap_err()
                .to_string()
                .contains("4 MiB")
        );
    }
    #[test]
    #[cfg(unix)]
    fn direct_scope_skips_links_and_protected_metadata() {
        use std::os::unix::fs::symlink;
        let root = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("scope")).unwrap();
        fs::create_dir(root.path().join("scope/.git")).unwrap();
        fs::write(root.path().join("scope/.git/private"), "needle").unwrap();
        fs::write(outside.path().join("private"), "needle").unwrap();
        symlink(outside.path(), root.path().join("scope/link")).unwrap();
        let workspace = Workspace::new(root.path()).unwrap();
        let found = search(
            &workspace,
            "needle",
            SearchMode::Literal,
            &FileSelector::exact("scope"),
            100,
            0,
        )
        .unwrap();
        assert!(found.ranges.is_empty());
        assert_eq!(
            found.coverage.unwrap()["protected_or_special_entries_skipped"],
            2
        );
    }
}
