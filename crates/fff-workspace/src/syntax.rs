//! Bounded, parser-backed Rust identifier selection. No symbol resolution.
use crate::Workspace;
use crate::search::{MatchSet, SearchMode, SearchRange};
use std::collections::BTreeMap;
use std::ops::ControlFlow;
use std::path::Path;
use std::time::{Duration, Instant};
use tree_sitter::{ParseOptions, Parser, Tree};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

fn parse(parser: &mut Parser, source: &[u8], deadline: Instant) -> Result<Tree> {
    if Instant::now() >= deadline {
        return Err("syntax selection exceeded 5-second budget".into());
    }
    let mut progress = |_: &tree_sitter::ParseState| {
        if Instant::now() >= deadline {
            ControlFlow::Break(())
        } else {
            ControlFlow::Continue(())
        }
    };
    let tree = parser
        .parse_with_options(
            &mut |offset, _| &source[offset..],
            None,
            Some(ParseOptions::new().progress_callback(&mut progress)),
        )
        .ok_or("syntax parsing cancelled or exceeded budget")?;
    if tree.root_node().has_error() {
        return Err("Rust source has parse errors; no selection created".into());
    }
    Ok(tree)
}

fn excluded(kind: &str) -> bool {
    matches!(
        kind,
        "line_comment"
            | "block_comment"
            | "doc_comment"
            | "string_literal"
            | "raw_string_literal"
            | "char_literal"
            | "token_tree"
            | "token_tree_pattern"
            | "macro_definition"
            | "attribute_item"
            | "inner_attribute_item"
            | "lifetime"
    )
}

/// Select exact identifier spellings from explicit Rust files. Output is complete
/// or an error, never a truncated mutation capability.
pub fn select_rust_identifiers(
    workspace: &Workspace,
    paths: &[String],
    identifier: &str,
    limit: usize,
) -> Result<MatchSet> {
    if paths.is_empty() || paths.len() > 32 || !(1..=1000).contains(&limit) {
        return Err("syntax selection requires 1..=32 files and limit 1..=1000".into());
    }
    if identifier.is_empty() || identifier.len() > 256 {
        return Err("identifier must be 1..=256 UTF-8 bytes".into());
    }
    // Tree-sitter accepts some keyword spellings as contextual identifiers.
    // Apply the Rust 2024 strict/reserved keyword rules explicitly.
    const KEYWORDS: &[&str] = &[
        "_", "as", "async", "await", "break", "const", "continue", "crate", "dyn", "else", "enum",
        "extern", "false", "fn", "for", "if", "impl", "in", "let", "loop", "match", "mod", "move",
        "mut", "pub", "ref", "return", "self", "Self", "static", "struct", "super", "trait",
        "true", "type", "unsafe", "use", "where", "while", "abstract", "become", "box", "do",
        "final", "gen", "macro", "override", "priv", "try", "typeof", "unsized", "virtual",
        "yield",
    ];
    if KEYWORDS.contains(&identifier)
        || matches!(
            identifier,
            "r#_" | "r#crate" | "r#self" | "r#Self" | "r#super"
        )
    {
        return Err("identifier is a reserved Rust 2024 spelling".into());
    }
    let deadline = Instant::now() + Duration::from_secs(5);
    let mut parser = Parser::new();
    parser.set_language(&tree_sitter_rust::LANGUAGE.into())?;
    let probe = format!("fn {identifier}() {{}}");
    let probe_tree = parse(&mut parser, probe.as_bytes(), deadline)?;
    let function = probe_tree
        .root_node()
        .named_child(0)
        .ok_or("invalid Rust identifier")?;
    let name = function
        .child_by_field_name("name")
        .ok_or("invalid Rust identifier")?;
    if function.kind() != "function_item"
        || name.kind() != "identifier"
        || name.utf8_text(probe.as_bytes())? != identifier
    {
        return Err("identifier must be one exact Rust identifier (raw spelling allowed)".into());
    }
    let mut ranges = Vec::new();
    let mut file_hashes = BTreeMap::new();
    for path in paths {
        if path.len() > 4096 || Path::new(path).extension().is_none_or(|ext| ext != "rs") {
            return Err("syntax selection paths must be .rs files of at most 4096 bytes".into());
        }
        let (normalized, source) = workspace.read_source_bounded(Path::new(path), 256 * 1024)?;
        std::str::from_utf8(&source)?;
        if file_hashes.contains_key(&normalized) {
            return Err("duplicate syntax selection path".into());
        }
        let tree = parse(&mut parser, &source, deadline).map_err(|e| format!("{path}: {e}"))?;
        let mut cursor = tree.walk();
        'walk: loop {
            if Instant::now() >= deadline {
                return Err("syntax selection exceeded 5-second budget".into());
            }
            let node = cursor.node();
            if matches!(
                node.kind(),
                "identifier"
                    | "type_identifier"
                    | "field_identifier"
                    | "shorthand_field_identifier"
            ) && node.utf8_text(&source)? == identifier
            {
                if ranges.len() == limit {
                    return Err("syntax match limit exceeded; narrow the file set".into());
                }
                ranges.push(SearchRange {
                    path: normalized.clone(),
                    start_byte: node.start_byte(),
                    end_byte: node.end_byte(),
                    line: node.start_position().row as u64 + 1,
                });
            }
            if !excluded(node.kind()) && cursor.goto_first_child() {
                continue;
            }
            loop {
                if cursor.goto_next_sibling() {
                    break;
                }
                if !cursor.goto_parent() {
                    break 'walk;
                }
            }
        }
        file_hashes.insert(normalized, blake3::hash(&source).to_hex().to_string());
    }
    ranges.sort();
    ranges.dedup();
    Ok(MatchSet {
        pattern: identifier.to_string(),
        mode: SearchMode::Literal,
        ranges,
        file_hashes,
        next_file_offset: 0,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::journal::Journal;
    use crate::transform;
    use std::fs;
    use std::sync::atomic::{AtomicU64, Ordering};

    fn fixture() -> std::path::PathBuf {
        static NEXT: AtomicU64 = AtomicU64::new(0);
        let root = std::env::temp_dir().join(format!(
            "ffsake-syntax-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&root).unwrap();
        root
    }

    #[test]
    fn parser_selects_code_identifiers_and_skips_opaque_text() {
        let root = fixture();
        let source = r##"// User comment
#[allow(User)]
struct User { User: i32 }
fn demo(User: User) -> User {
    let text = "User";
    let raw = r#"User"#;
    let _ = r#User;
    let _ = User.User;
    consume!(User);
    User { User }
}
macro_rules! User { () => { User } }
"##;
        fs::write(root.join("code.rs"), source).unwrap();
        let ws = Workspace::new(&root).unwrap();
        let set = select_rust_identifiers(&ws, &["code.rs".into()], "User", 100).unwrap();
        assert_eq!(set.ranges.len(), 9, "{:?}", set.ranges);
        for range in &set.ranges {
            assert_eq!(&source[range.start_byte..range.end_byte], "User");
            assert!(![1, 2, 5, 6, 7, 9, 12].contains(&range.line));
        }
        let raw = select_rust_identifiers(&ws, &["code.rs".into()], "r#User", 100).unwrap();
        assert_eq!(raw.ranges.len(), 1);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn unicode_crlf_ranges_reuse_hash_guards_and_history() {
        let root = fixture();
        let source = "// 🚀 café\r\nfn demo() { let café = 1; let _ = café; }\r\n";
        fs::write(root.join("code.rs"), source).unwrap();
        let ws = Workspace::new(&root).unwrap();
        let set = select_rust_identifiers(&ws, &["code.rs".into()], "café", 10).unwrap();
        assert_eq!(set.ranges.len(), 2);
        assert_eq!(
            set.file_hashes["code.rs"],
            blake3::hash(source.as_bytes()).to_hex().to_string()
        );
        set.validate(&ws).unwrap();
        let plan = set.replace_transform(&ws, "coffee").unwrap();
        let result = transform::execute(&ws, &plan, None).unwrap();
        assert_eq!(
            fs::read_to_string(root.join("code.rs")).unwrap(),
            "// 🚀 café\r\nfn demo() { let coffee = 1; let _ = coffee; }\r\n"
        );
        assert!(set.validate(&ws).is_err());
        Journal::open(&root)
            .unwrap()
            .undo(&result.transaction.unwrap().id)
            .unwrap();
        assert_eq!(fs::read_to_string(root.join("code.rs")).unwrap(), source);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn syntax_selection_rejects_incomplete_inputs_and_limits() {
        let root = fixture();
        let ws = Workspace::new(&root).unwrap();
        fs::write(root.join("code.rs"), "fn f() { let user = 1; user; }").unwrap();
        for identifier in [
            "",
            "user() {} fn other",
            "user ",
            "fn",
            "1user",
            "gen",
            "r#self",
            "r#_",
        ] {
            assert!(
                select_rust_identifiers(&ws, &["code.rs".into()], identifier, 10).is_err(),
                "{identifier}"
            );
        }
        assert!(select_rust_identifiers(&ws, &["code.rs".into()], "user", 1).is_err());
        assert!(
            select_rust_identifiers(&ws, &["code.rs".into(), "./code.rs".into()], "user", 10)
                .is_err()
        );
        for paths in [
            vec![],
            vec!["code.rs".into(); 33],
            vec!["missing.rs".into()],
            vec!["../code.rs".into()],
            vec![".ffsake/code.rs".into()],
        ] {
            assert!(select_rust_identifiers(&ws, &paths, "user", 10).is_err());
        }
        for bytes in [b"fn f( {".to_vec(), vec![0xff], vec![b' '; 256 * 1024 + 1]] {
            fs::write(root.join("code.rs"), bytes).unwrap();
            assert!(select_rust_identifiers(&ws, &["code.rs".into()], "user", 10).is_err());
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn parser_budget_can_cancel_without_a_partial_tree() {
        let mut parser = Parser::new();
        parser
            .set_language(&tree_sitter_rust::LANGUAGE.into())
            .unwrap();
        assert!(parse(&mut parser, b"fn f() {}", Instant::now()).is_err());
    }
}
