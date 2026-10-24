//! Explicit local crate graphs, independent of Cargo discovery and execution.
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CrateSpec {
    pub root: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub edition: Option<String>,
    #[serde(default)]
    pub dependencies: BTreeMap<String, String>,
    #[serde(default)]
    pub features: Vec<String>,
    #[serde(default)]
    pub cfg_flags: Vec<String>,
}

#[derive(Clone, Copy)]
pub struct RustContext<'a> {
    pub edition: &'a str,
    pub crates: &'a [CrateSpec],
}

impl<'a> From<&'a str> for RustContext<'a> {
    fn from(edition: &'a str) -> Self {
        Self {
            edition,
            crates: &[],
        }
    }
}

fn dependency_name(name: &str) -> bool {
    // Deliberately restrict the API to ordinary ASCII crate names, excluding
    // Rust keywords. Raw/Unicode dependency spellings are not accepted here.
    !name.is_empty()
        && name.len() <= 128
        && name != "_"
        && name.bytes().enumerate().all(|(index, b)| {
            b == b'_' || b.is_ascii_alphabetic() || (index > 0 && b.is_ascii_digit())
        })
        && ![
            "as", "async", "await", "break", "const", "continue", "crate", "dyn", "else", "enum",
            "extern", "false", "fn", "for", "if", "impl", "in", "let", "loop", "match", "mod",
            "move", "mut", "pub", "ref", "return", "self", "Self", "static", "struct", "super",
            "trait", "true", "type", "unsafe", "use", "where", "while", "abstract", "become",
            "box", "do", "final", "gen", "macro", "override", "priv", "try", "typeof", "unsized",
            "virtual", "yield",
        ]
        .contains(&name)
}

fn explicit_cfg(krate: &CrateSpec) -> Result<Vec<String>> {
    if krate.features.len() > 32 || krate.cfg_flags.len() > 32 {
        return Err("crate exceeds 32 features or 32 custom cfg flags".into());
    }
    let mut seen = BTreeSet::new();
    let mut result = Vec::new();
    for feature in &krate.features {
        if feature.is_empty()
            || feature.len() > 128
            || !feature
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-' | b'+'))
            || !seen.insert(feature)
        {
            return Err("invalid or duplicate explicit feature name".into());
        }
        result.push(format!("feature=\"{feature}\""));
    }
    seen.clear();
    for flag in &krate.cfg_flags {
        if !dependency_name(flag)
            || flag.starts_with("target_")
            || [
                "unix",
                "windows",
                "debug_assertions",
                "proc_macro",
                "test",
                "panic",
                "rust_analyzer",
                "miri",
            ]
            .contains(&flag.as_str())
            || !seen.insert(flag)
        {
            return Err("invalid, reserved or duplicate custom cfg flag".into());
        }
        result.push(flag.clone());
    }
    Ok(result)
}

pub(crate) fn graph(
    snapshot: &Path,
    names: &BTreeMap<&str, String>,
    primary: &str,
    default_edition: &str,
    explicit: &[CrateSpec],
) -> Result<Vec<Value>> {
    let default = [CrateSpec {
        root: primary.into(),
        edition: None,
        dependencies: BTreeMap::new(),
        features: vec![],
        cfg_flags: vec![],
    }];
    let crates = if explicit.is_empty() {
        &default[..]
    } else {
        explicit
    };
    if crates.len() > 16 {
        return Err("explicit Rust project exceeds 16 crates".into());
    }
    let mut indexes = BTreeMap::new();
    let mut roots = BTreeSet::new();
    for (index, krate) in crates.iter().enumerate() {
        let normalized = names
            .get(krate.root.as_str())
            .ok_or("crate root is not an explicit source file")?;
        if !roots.insert(normalized) || indexes.insert(krate.root.as_str(), index).is_some() {
            return Err("duplicate Rust crate root".into());
        }
        if krate.dependencies.len() > 16 {
            return Err("crate exceeds 16 dependencies".into());
        }
        if !matches!(
            krate.edition.as_deref().unwrap_or(default_edition),
            "2015" | "2018" | "2021" | "2024"
        ) {
            return Err("unsupported crate edition".into());
        }
    }
    if !indexes.contains_key(primary) {
        return Err("explicit project must include crate_root".into());
    }
    let edges: usize = crates.iter().map(|krate| krate.dependencies.len()).sum();
    if edges > 64 {
        return Err("explicit Rust project exceeds 64 dependency edges".into());
    }
    let mut graph = Vec::new();
    let mut adjacency = vec![Vec::new(); crates.len()];
    for (index, krate) in crates.iter().enumerate() {
        let mut dependencies = Vec::new();
        for (name, target) in &krate.dependencies {
            if !dependency_name(name) {
                return Err("invalid explicit Rust dependency name".into());
            }
            let dependency = *indexes
                .get(target.as_str())
                .ok_or("dependency target must be a declared crate root")?;
            adjacency[index].push(dependency);
            dependencies.push(json!({"crate":dependency,"name":name}));
        }
        graph.push(
            json!({"root_module":snapshot.join(&names[krate.root.as_str()]),
            "edition":krate.edition.as_deref().unwrap_or(default_edition),"deps":dependencies,
            "cfg":explicit_cfg(krate)?,"env":{},"is_workspace_member":true,
            "source":{"include_dirs":[snapshot],"exclude_dirs":[]}}),
        );
    }
    fn visit(index: usize, graph: &[Vec<usize>], states: &mut [u8]) -> bool {
        if states[index] == 1 {
            return false;
        }
        if states[index] == 2 {
            return true;
        }
        states[index] = 1;
        for &dependency in &graph[index] {
            if !visit(dependency, graph, states) {
                return false;
            }
        }
        states[index] = 2;
        true
    }
    let mut states = vec![0; crates.len()];
    for index in 0..crates.len() {
        if !visit(index, &adjacency, &mut states) {
            return Err("cyclic Rust crate dependencies".into());
        }
    }
    Ok(graph)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn specs() -> Vec<CrateSpec> {
        vec![
            CrateSpec {
                root: "app.rs".into(),
                edition: None,
                dependencies: BTreeMap::from([("account_model".into(), "model.rs".into())]),
                features: vec![],
                cfg_flags: vec![],
            },
            CrateSpec {
                root: "model.rs".into(),
                edition: Some("2021".into()),
                dependencies: BTreeMap::new(),
                features: vec![],
                cfg_flags: vec![],
            },
        ]
    }

    #[test]
    fn features_and_custom_flags_are_literal_bounded_configuration() {
        let mut krate = specs().remove(1);
        krate.features = vec!["fast".into(), "serde-json".into()];
        krate.cfg_flags = vec!["local_mode".into()];
        assert_eq!(
            explicit_cfg(&krate).unwrap(),
            vec!["feature=\"fast\"", "feature=\"serde-json\"", "local_mode"]
        );
        for feature in [
            "",
            "dep/feature",
            "dep:feature",
            "quoted\"",
            "white space",
            "café",
            &"x".repeat(129),
        ] {
            let mut invalid = krate.clone();
            invalid.features = vec![feature.into()];
            assert!(explicit_cfg(&invalid).is_err());
        }
        for flag in [
            "unix",
            "windows",
            "target_os",
            "debug_assertions",
            "test",
            "rust_analyzer",
            "feature=\"x\"",
            "foo-bar",
        ] {
            let mut invalid = krate.clone();
            invalid.cfg_flags = vec![flag.into()];
            assert!(explicit_cfg(&invalid).is_err());
        }
        let mut duplicate = krate.clone();
        duplicate.features.push("fast".into());
        assert!(explicit_cfg(&duplicate).is_err());
        duplicate = krate.clone();
        duplicate.cfg_flags.push("local_mode".into());
        assert!(explicit_cfg(&duplicate).is_err());
        for feature_list in [true, false] {
            let mut many = krate.clone();
            let values = (0..33).map(|n| format!("name{n}")).collect();
            if feature_list {
                many.features = values;
            } else {
                many.cfg_flags = values;
            }
            assert!(explicit_cfg(&many).is_err());
        }
        // The total edge budget is independent of per-crate dependency bounds.
        let roots: Vec<_> = (0..6).map(|n| format!("crate{n}.rs")).collect();
        let names = roots
            .iter()
            .map(|root| (root.as_str(), root.clone()))
            .collect();
        let graph_specs: Vec<_> = roots
            .iter()
            .enumerate()
            .map(|(index, root)| CrateSpec {
                root: root.clone(),
                edition: None,
                features: vec![],
                cfg_flags: vec![],
                dependencies: if index == 5 {
                    BTreeMap::new()
                } else {
                    (0..16)
                        .map(|n| (format!("alias{n}"), roots[5].clone()))
                        .collect()
                },
            })
            .collect();
        assert!(
            graph(
                &std::env::temp_dir(),
                &names,
                &roots[0],
                "2024",
                &graph_specs
            )
            .unwrap_err()
            .to_string()
            .contains("64 dependency edges")
        );
    }

    #[test]
    fn explicit_graph_maps_only_declared_roots_and_dependency_names() {
        let names = BTreeMap::from([("app.rs", "app.rs".into()), ("model.rs", "model.rs".into())]);
        let root = std::env::temp_dir();
        let result = graph(&root, &names, "app.rs", "2024", &specs()).unwrap();
        assert_eq!(
            result[0]["deps"],
            json!([{"crate":1,"name":"account_model"}])
        );
        assert_eq!(result[1]["edition"], "2021");
        assert_eq!(result[1]["root_module"], json!(root.join("model.rs")));
        let single = graph(&root, &names, "app.rs", "2024", &[]).unwrap();
        assert_eq!(single.len(), 1);
        assert_eq!(single[0]["deps"], json!([]));
    }

    #[test]
    fn invalid_graphs_reject_without_starting_a_server() {
        let names = BTreeMap::from([("app.rs", "app.rs".into()), ("model.rs", "model.rs".into())]);
        let root = std::env::temp_dir();
        let valid = specs();
        let mut variants = Vec::new();
        let mut cycle = valid.clone();
        cycle[1].dependencies.insert("app".into(), "app.rs".into());
        variants.push(cycle);
        let mut self_edge = valid.clone();
        self_edge[0]
            .dependencies
            .insert("app".into(), "app.rs".into());
        variants.push(self_edge);
        let mut missing = valid.clone();
        missing[0]
            .dependencies
            .insert("missing".into(), "elsewhere.rs".into());
        variants.push(missing);
        let mut outside = valid.clone();
        outside[1].root = "../outside.rs".into();
        variants.push(outside);
        let mut duplicate = valid.clone();
        duplicate.push(valid[0].clone());
        variants.push(duplicate);
        variants.push(vec![valid[1].clone()]);
        let mut edition = valid.clone();
        edition[1].edition = Some("2030".into());
        variants.push(edition);
        for name in [
            "self",
            "type",
            "gen",
            "_",
            "1bad",
            "hyphen-name",
            "r#type",
            "café",
            &"x".repeat(129),
        ] {
            let mut invalid = valid.clone();
            invalid[0].dependencies = BTreeMap::from([(name.into(), "model.rs".into())]);
            variants.push(invalid);
        }
        let mut many = valid.clone();
        many[0].dependencies = (0..17)
            .map(|n| (format!("name{n}"), "model.rs".into()))
            .collect();
        variants.push(many);
        variants.push(vec![valid[0].clone(); 17]);
        for invalid in variants {
            assert!(graph(&root, &names, "app.rs", "2024", &invalid).is_err());
        }
    }
}
