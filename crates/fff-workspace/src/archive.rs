//! Read-only ZIP/JAR snapshots. Never extracts members or executes archive content.
use crate::{Workspace, journal::Journal};
use serde_json::{Value, json};
use std::{
    collections::BTreeSet,
    fs,
    io::{Cursor, Read},
};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
const ARCHIVE_BYTES: u64 = 32 * 1024 * 1024;
const ENTRY_BYTES: u64 = 1024 * 1024;
const OUTPUT_BYTES: usize = 256 * 1024;
fn word(b: &[u8], at: usize) -> usize {
    u16::from_le_bytes([b[at], b[at + 1]]) as usize
}
fn dword(b: &[u8], at: usize) -> usize {
    u32::from_le_bytes(b[at..at + 4].try_into().unwrap()) as usize
}
fn name(value: &str) -> Result<()> {
    let trimmed = value.strip_suffix('/').unwrap_or(value);
    if value.len() > 4096
        || trimmed.is_empty()
        || value.contains(['\\', ':'])
        || value.chars().any(char::is_control)
        || trimmed
            .split('/')
            .any(|p| p.is_empty() || p == "." || p == "..")
    {
        return Err("unsafe archive entry path (absolute/traversal/ambiguous name)".into());
    }
    Ok(())
}
// Bound and validate the central directory BEFORE the ZIP library allocates it.
// Deliberately support single-disk ZIP32 only, without SFX prefixes/trailing data.
fn preflight(bytes: &[u8]) -> Result<()> {
    let eocd = (bytes.len().saturating_sub(65557)..bytes.len().saturating_sub(21))
        .rev()
        .find(|&i| {
            bytes[i..].starts_with(b"PK\x05\x06") && i + 22 + word(bytes, i + 20) == bytes.len()
        })
        .ok_or("invalid ZIP end record")?;
    let count = word(bytes, eocd + 10);
    let size = dword(bytes, eocd + 12);
    let mut at = dword(bytes, eocd + 16);
    if word(bytes, eocd + 4) != 0
        || word(bytes, eocd + 6) != 0
        || word(bytes, eocd + 8) != count
        || count > 4096
        || size > 2 * 1024 * 1024
        || at.checked_add(size) != Some(eocd)
    {
        return Err("archive exceeds 4096 entries/2 MiB directory or uses unsupported ZIP64/multi-disk layout".into());
    }
    if (count == 0 && eocd != 0) || (count > 0 && !bytes.starts_with(b"PK\x03\x04")) {
        return Err("prefixed/self-extracting archives are unsupported".into());
    }
    let mut names = BTreeSet::new();
    let mut expanded = 0u64;
    for _ in 0..count {
        if at.checked_add(46).is_none_or(|end| end > eocd) || &bytes[at..at + 4] != b"PK\x01\x02" {
            return Err("invalid ZIP directory".into());
        }
        let n = word(bytes, at + 28);
        let end = at + 46 + n + word(bytes, at + 30) + word(bytes, at + 32);
        if end > eocd {
            return Err("truncated ZIP directory".into());
        }
        let entry = std::str::from_utf8(&bytes[at + 46..at + 46 + n])?;
        name(entry)?;
        if !names.insert(entry.trim_end_matches('/')) {
            return Err("duplicate archive entry".into());
        }
        let compressed = dword(bytes, at + 20) as u64;
        let uncompressed = dword(bytes, at + 24) as u64;
        expanded = expanded
            .checked_add(uncompressed)
            .ok_or("archive size overflow")?;
        if expanded > 128 * 1024 * 1024 || uncompressed > compressed.saturating_mul(200).max(1024) {
            return Err(
                "archive exceeds 128 MiB declared expansion or 200:1 compression ratio".into(),
            );
        }
        if word(bytes, at + 8) & 1 != 0
            || ![0, 8].contains(&word(bytes, at + 10))
            || word(bytes, at + 34) != 0
        {
            return Err(
                "encrypted, multi-disk or unsupported compression (only stored/deflate supported)"
                    .into(),
            );
        }
        at = end;
    }
    if at != eocd {
        return Err("ZIP entry count/directory length mismatch".into());
    }
    Ok(())
}
fn snapshot(workspace: &Workspace, path: &str) -> Result<Vec<u8>> {
    if !["zip", "jar"].contains(
        &path
            .rsplit('.')
            .next()
            .unwrap_or("")
            .to_ascii_lowercase()
            .as_str(),
    ) {
        return Err("archive path must end in .zip or .jar".into());
    }
    let resolved = Journal::open(workspace.root())?.resolve(path)?;
    let metadata = fs::symlink_metadata(&resolved)?;
    if !metadata.is_file() || metadata.len() > ARCHIVE_BYTES {
        return Err("archive must be a regular file <=32 MiB".into());
    }
    let mut bytes = Vec::new();
    fs::File::open(resolved)?
        .take(ARCHIVE_BYTES + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 > ARCHIVE_BYTES {
        return Err("archive exceeds 32 MiB".into());
    }
    Ok(bytes)
}
pub(crate) fn browse(
    workspace: &Workspace,
    path: &str,
    entry: Option<&str>,
    offset: usize,
    limit: usize,
    lines: (usize, Option<usize>),
    expected_hash: Option<&str>,
) -> Result<Value> {
    let (start, end) = lines;
    if limit == 0 || limit > 200 || start == 0 {
        return Err("limit must be 1..=200; start is one-based".into());
    }
    let bytes = snapshot(workspace, path)?;
    let hash = blake3::hash(&bytes).to_hex().to_string();
    if expected_hash.is_some_and(|expected| expected != hash) {
        return Err("archive changed since listing/read".into());
    }
    preflight(&bytes)?;
    let mut archive = zip::ZipArchive::new(Cursor::new(&bytes))?;
    archive.validate_header_consistency()?;
    if archive.len() > 4096 {
        return Err("archive exceeds entry limit".into());
    }
    let mut decoded_names = BTreeSet::new();
    for index in 0..archive.len() {
        let item = archive.by_index_raw(index)?;
        let decoded = item.name()?;
        name(&decoded)?;
        if !decoded_names.insert(decoded.trim_end_matches('/').to_owned()) {
            return Err("duplicate decoded archive entry".into());
        }
        if item
            .unix_mode()
            .is_some_and(|m| ![0, 0o100000, 0o040000].contains(&(m & 0o170000)))
        {
            return Err("archive contains a symlink or special entry".into());
        }
    }
    let mut response = if let Some(entry) = entry {
        name(entry)?;
        if offset != 0 {
            return Err("entry reads require offset=0".into());
        }
        let mut item = archive.by_name(entry)?;
        if item.is_dir() || item.size() > ENTRY_BYTES {
            return Err("source entry must be a file <=1 MiB decompressed".into());
        }
        let declared = item.size();
        let mut data = Vec::new();
        (&mut item).take(ENTRY_BYTES + 1).read_to_end(&mut data)?;
        if data.len() as u64 != declared || data.len() as u64 > ENTRY_BYTES {
            return Err("entry decompression size mismatch/limit".into());
        }
        if data.contains(&0) {
            return Err("binary entry; only UTF-8 source/text reads are supported".into());
        }
        let text = std::str::from_utf8(&data)?;
        let lines = text.split_inclusive('\n').collect::<Vec<_>>();
        let end = end.unwrap_or(start.saturating_add(199));
        if end < start || end - start >= 2000 || start > lines.len().saturating_add(1) {
            return Err("invalid line range; at most 2000 lines".into());
        }
        let output = lines
            .iter()
            .skip(start - 1)
            .take(end - start + 1)
            .copied()
            .collect::<String>();
        json!({"entry":entry,"text":output,"start":start,"end":end.min(lines.len()),"total_lines":lines.len(),"entry_hash":blake3::hash(&data).to_hex().to_string(),"decompressed_bytes":data.len()})
    } else {
        if offset > archive.len() {
            return Err("archive offset exceeds entry count".into());
        }
        let mut entries = Vec::new();
        for index in offset..archive.len().min(offset.saturating_add(limit)) {
            let item = archive.by_index_raw(index)?;
            entries.push(json!({"name":item.name()?,"directory":item.is_dir(),"bytes":item.size(),"compressed_bytes":item.compressed_size()}));
        }
        json!({"entries":entries,"total_entries":archive.len(),"next_offset":if offset+limit<archive.len(){Some(offset+limit)}else{None}})
    };
    response["archive_hash"] = json!(hash);
    response["read_only"] = json!(true);
    response["archive"] = json!(path);
    if serde_json::to_vec(&response)?.len() > OUTPUT_BYTES {
        return Err("archive output exceeds 256 KiB; reduce list limit or line range".into());
    }
    if snapshot(workspace, path)? != bytes {
        return Err("archive changed during read".into());
    }
    Ok(response)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn short_and_forged_directories_fail_without_panicking() {
        for length in 0..100 {
            assert!(preflight(&vec![0; length]).is_err());
            let mut bytes = vec![0; length];
            if length >= 4 {
                bytes[..4].copy_from_slice(b"PK\x05\x06");
            }
            if length != 22 {
                assert!(preflight(&bytes).is_err());
            }
        }
        let mut empty = vec![0; 22];
        empty[..4].copy_from_slice(b"PK\x05\x06");
        assert!(preflight(&empty).is_ok());
        empty[10..12].copy_from_slice(&4097u16.to_le_bytes());
        assert!(preflight(&empty).is_err());
    }
    #[test]
    fn names_reject_portable_traversal_and_ambiguity() {
        for value in [
            "", "/a", "../a", "a/../b", "a/./b", "a//b", "C:a", "a\\b", "a\0b", "a\nb",
        ] {
            assert!(name(value).is_err(), "{value:?}");
        }
        for value in ["demo/Planner.java", "demo/", "café.java"] {
            assert!(name(value).is_ok());
        }
    }
}
