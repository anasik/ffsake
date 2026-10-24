//! Read-only action discovery. Unsupported requirements never expose partial edits.
use crate::coordinates::{Utf16Position, from_utf16_position};
use crate::edits::{RangeEdit, apply_range_edits};
use crate::lsp::snapshot_path;
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

fn text<'a>(value: &'a Value, maximum: usize, bytes: &mut usize) -> Result<&'a str> {
    let value = value.as_str().ok_or("invalid code-action text")?;
    *bytes += value.len();
    if value.len() > maximum || *bytes > 65_536 {
        return Err("code-action text budget exceeded; no partial result returned".into());
    }
    Ok(value)
}

pub(crate) fn actions(
    root: &Path,
    sources: &BTreeMap<String, String>,
    value: Value,
    limit: usize,
) -> Result<Value> {
    let items = match &value {
        Value::Null => &[][..],
        Value::Array(items) => items.as_slice(),
        _ => return Err("invalid code-action response".into()),
    };
    if items.len() > limit {
        return Err("code-action limit exceeded; no partial result returned".into());
    }
    let mut bytes = 0;
    let mut edit_count = 0;
    let mut output = Vec::new();
    for item in items {
        let title = text(&item["title"], 4096, &mut bytes)?;
        if title.is_empty() {
            return Err("empty code-action title".into());
        }
        let kind = match item.get("kind") {
            None | Some(Value::Null) => Value::Null,
            Some(value) => json!(text(value, 256, &mut bytes)?),
        };
        let preferred = match item.get("isPreferred") {
            None | Some(Value::Null) => false,
            Some(value) => value.as_bool().ok_or("invalid preferred flag")?,
        };
        let mut reasons = BTreeSet::new();
        let disabled = match item.get("disabled") {
            None | Some(Value::Null) => Value::Null,
            Some(value) => {
                reasons.insert("disabled");
                json!(text(&value["reason"], 4096, &mut bytes)?)
            }
        };
        if let Some(command) = item.get("command")
            && !command.is_null()
        {
            let name = if command.is_string() {
                command
            } else {
                &command["command"]
            };
            text(name, 256, &mut bytes)?;
            reasons.insert("command");
        }
        // Keep the entire action unsupported if any edit requires more than plain
        // source text. Do not expose the harmless-looking subset of a larger action.
        let mut documents: Vec<(&str, &Vec<Value>)> = Vec::new();
        if let Some(edit) = item.get("edit").filter(|value| !value.is_null()) {
            if !edit.is_object() {
                return Err("invalid workspace edit".into());
            }
            if let Some(annotations) = edit.get("changeAnnotations")
                && !annotations.is_null()
            {
                let annotations = annotations
                    .as_object()
                    .ok_or("invalid change annotations")?;
                if !annotations.is_empty() {
                    reasons.insert("annotations");
                }
            }
            let changes = edit.get("changes").filter(|v| !v.is_null());
            let document_changes = edit.get("documentChanges").filter(|v| !v.is_null());
            if changes.is_some() && document_changes.is_some() {
                return Err("ambiguous workspace edit representations".into());
            }
            if let Some(changes) = changes {
                let changes = changes.as_object().ok_or("invalid workspace changes")?;
                if changes.len() > 32 {
                    return Err("too many action documents".into());
                }
                for (uri, edits) in changes {
                    documents.push((uri, edits.as_array().ok_or("invalid text edits")?));
                }
            }
            if let Some(changes) = document_changes {
                let changes = changes.as_array().ok_or("invalid document changes")?;
                if changes.len() > 1000 {
                    return Err("too many document changes".into());
                }
                for change in changes {
                    if let Some(kind) = change.get("kind") {
                        if !matches!(kind.as_str(), Some("create" | "rename" | "delete")) {
                            return Err("unknown resource operation".into());
                        }
                        reasons.insert("resource_operation");
                        continue;
                    }
                    let document = &change["textDocument"];
                    if !document["version"].is_null() && document["version"] != 1 {
                        return Err("code-action document version differs from snapshot".into());
                    }
                    documents.push((
                        document["uri"].as_str().ok_or("missing action URI")?,
                        change["edits"].as_array().ok_or("invalid text edits")?,
                    ));
                }
            }
        } else {
            reasons.insert(if item.get("data").is_some_and(|data| !data.is_null()) {
                "unresolved"
            } else {
                "no_edit"
            });
        }
        let action_edit_count: usize = documents.iter().map(|(_, edits)| edits.len()).sum();
        edit_count += action_edit_count;
        if edit_count > 1000 {
            return Err("code-action edit budget exceeded".into());
        }
        for (_, edits) in &documents {
            for edit in *edits {
                text(&edit["newText"], 65_536, &mut bytes)?;
                if edit.get("annotationId").is_some_and(|v| !v.is_null()) {
                    reasons.insert("annotations");
                }
                if let Some(format) = edit.get("insertTextFormat").filter(|v| !v.is_null()) {
                    match format.as_u64() {
                        Some(1) => (),
                        Some(2) => {
                            reasons.insert("snippet");
                        }
                        _ => return Err("invalid action text format".into()),
                    }
                }
            }
        }
        if action_edit_count == 0 {
            reasons.insert("no_edit");
        }
        let mut normalized = Vec::new();
        if reasons.is_empty() {
            let mut seen = BTreeSet::new();
            for (uri, edits) in documents {
                let path = snapshot_path(root, sources, uri)?;
                if !seen.insert(path.clone()) {
                    return Err("duplicate action document".into());
                }
                let source = &sources[&path];
                let edits = edits
                    .iter()
                    .map(|edit| -> Result<RangeEdit> {
                        let start: Utf16Position =
                            serde_json::from_value(edit["range"]["start"].clone())?;
                        let end: Utf16Position =
                            serde_json::from_value(edit["range"]["end"].clone())?;
                        Ok(RangeEdit {
                            start_byte: from_utf16_position(source, start)?,
                            end_byte: from_utf16_position(source, end)?,
                            replacement: edit["newText"]
                                .as_str()
                                .ok_or("invalid text edit")?
                                .to_owned(),
                        })
                    })
                    .collect::<Result<Vec<_>>>()?;
                if apply_range_edits(source, &edits)?.len() > 256 * 1024 {
                    return Err("code action would exceed the source-size bound".into());
                }
                for edit in edits {
                    normalized.push(json!({"path":path,"start_byte":edit.start_byte,
                        "end_byte":edit.end_byte,"replacement":edit.replacement}));
                }
            }
        }
        output.push(json!({"title":title,"kind":kind,"is_preferred":preferred,
            "disabled_reason":disabled,"unsupported_reasons":reasons,"text_edit_count":action_edit_count,
            "edits":if reasons.is_empty() { json!(normalized) } else { Value::Null }}));
    }
    Ok(json!({"actions":output,"action_count":output.len(),"text_bytes":bytes}))
}

/// Only resident-owned discovery reports reach this function. The request chooses
/// an exact frozen action; it cannot supply replacements or alter analysis hashes.
pub(crate) fn preview_transform(
    workspace: &crate::Workspace,
    report: &Value,
    index: usize,
    expected_title: &str,
    expected_edits: usize,
) -> Result<crate::transform::Transform> {
    use crate::transform::{Mutation, TargetedRangeEdit, Transform};
    use crate::{FileSelector, PathSelector};
    let action = report["actions"]
        .as_array()
        .and_then(|actions| actions.get(index))
        .ok_or("unknown code-action index")?;
    if action["title"] != expected_title {
        return Err("code-action title differs from expected identity".into());
    }
    if action["unsupported_reasons"]
        .as_array()
        .is_none_or(|reasons| !reasons.is_empty())
    {
        return Err("unsupported code action cannot become a preview".into());
    }
    let edits = action["edits"]
        .as_array()
        .ok_or("code action has no text edits")?;
    if expected_edits == 0 || edits.len() != expected_edits || edits.len() > 1000 {
        return Err("unexpected code-action edit count".into());
    }
    let hashes: BTreeMap<String, String> = serde_json::from_value(report["file_hashes"].clone())?;
    if hashes.is_empty() || hashes.len() > 32 {
        return Err("invalid action source hashes".into());
    }
    let edits = edits
        .iter()
        .map(|edit| -> Result<TargetedRangeEdit> {
            let path = edit["path"].as_str().ok_or("missing edit path")?;
            if !hashes.contains_key(path) {
                return Err("action path lacks source provenance".into());
            }
            Ok(TargetedRangeEdit {
                path: path.into(),
                edit: RangeEdit {
                    start_byte: serde_json::from_value(edit["start_byte"].clone())?,
                    end_byte: serde_json::from_value(edit["end_byte"].clone())?,
                    replacement: edit["replacement"]
                        .as_str()
                        .ok_or("missing action replacement")?
                        .into(),
                },
            })
        })
        .collect::<Result<Vec<_>>>()?;
    let plan = Transform {
        target: FileSelector {
            include: hashes.keys().cloned().map(PathSelector::Exact).collect(),
            exclude: vec![],
        },
        operations: vec![
            Mutation::AssertHashes {
                expected: hashes.clone(),
            },
            Mutation::ApplyRanges { edits },
        ],
        max_files: Some(hashes.len()),
    };
    crate::syntax::validate_transform(workspace, &plan, &hashes)?;
    Ok(plan)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn action_preview_guards_all_inputs_and_reuses_atomic_history() {
        let directory = tempfile::tempdir().unwrap();
        let root = directory.path();
        let main = "mod other; fn main() { let count = 1; let _ = count; }\r\n";
        let other = "pub const VALUE: u32 = 1;\r\n";
        let context = "pub fn context() {}\n";
        for (path, source) in [
            ("main.rs", main),
            ("other.rs", other),
            ("context.rs", context),
        ] {
            std::fs::write(root.join(path), source).unwrap();
        }
        let workspace = crate::Workspace::new(root).unwrap();
        let hashes: BTreeMap<_, _> = ["main.rs", "other.rs", "context.rs"]
            .into_iter()
            .map(|path| (path.to_string(), workspace.content_hash(path).unwrap()))
            .collect();
        let report = json!({"file_hashes":hashes,"actions":[{"title":"Two-file proposal","unsupported_reasons":[],"edits":[
            {"path":"main.rs","start_byte":main.find("count").unwrap()+5,"end_byte":main.find("count").unwrap()+5,"replacement":": u32"},
            {"path":"other.rs","start_byte":other.find('1').unwrap(),"end_byte":other.find('1').unwrap()+1,"replacement":"2"}]}]});
        assert!(preview_transform(&workspace, &report, 1, "Two-file proposal", 2).is_err());
        assert!(preview_transform(&workspace, &report, 0, "Wrong title", 2).is_err());
        assert!(preview_transform(&workspace, &report, 0, "Two-file proposal", 1).is_err());
        let plan = preview_transform(&workspace, &report, 0, "Two-file proposal", 2).unwrap();
        assert_eq!(
            std::fs::read(root.join("main.rs")).unwrap(),
            main.as_bytes()
        );
        // Even a file with no edits is part of the analysis provenance at both stages.
        std::fs::write(root.join("context.rs"), "pub fn changed() {}\n").unwrap();
        assert!(preview_transform(&workspace, &report, 0, "Two-file proposal", 2).is_err());
        assert!(crate::transform::execute(&workspace, &plan, None).is_err());
        assert_eq!(
            std::fs::read(root.join("main.rs")).unwrap(),
            main.as_bytes()
        );
        assert_eq!(
            std::fs::read(root.join("other.rs")).unwrap(),
            other.as_bytes()
        );
        std::fs::write(root.join("context.rs"), context).unwrap();
        let mut invalid = report.clone();
        invalid["actions"][0]["edits"][0]["replacement"] = json!("}");
        assert!(preview_transform(&workspace, &invalid, 0, "Two-file proposal", 2).is_err());
        let mut unsupported = report.clone();
        unsupported["actions"][0]["unsupported_reasons"] = json!(["command"]);
        assert!(preview_transform(&workspace, &unsupported, 0, "Two-file proposal", 2).is_err());
        let result = crate::transform::execute(&workspace, &plan, None).unwrap();
        assert_eq!(result.changed_files, 2);
        let transaction = result.transaction.unwrap().id;
        let after_main = std::fs::read(root.join("main.rs")).unwrap();
        let after_other = std::fs::read(root.join("other.rs")).unwrap();
        let journal = crate::journal::Journal::open(root).unwrap();
        journal.undo(&transaction).unwrap();
        assert_eq!(
            std::fs::read(root.join("main.rs")).unwrap(),
            main.as_bytes()
        );
        assert_eq!(
            std::fs::read(root.join("other.rs")).unwrap(),
            other.as_bytes()
        );
        journal.redo(&transaction).unwrap();
        assert_eq!(std::fs::read(root.join("main.rs")).unwrap(), after_main);
        assert_eq!(std::fs::read(root.join("other.rs")).unwrap(), after_other);
        assert_eq!(
            std::fs::read(root.join("context.rs")).unwrap(),
            context.as_bytes()
        );
    }

    fn fixture() -> (std::path::PathBuf, BTreeMap<String, String>, Value) {
        let root = std::env::temp_dir().join("ffsake-action-normalizer");
        let uri = url::Url::from_file_path(root.join("lib.rs"))
            .unwrap()
            .to_string();
        let item = json!({"title":"Insert type","kind":"refactor.rewrite","edit":{"documentChanges":[{
            "textDocument":{"uri":uri,"version":1},"edits":[{"range":{
                "start":{"line":0,"character":6},"end":{"line":0,"character":6}},"newText":": u32"}]}]}});
        (
            root,
            BTreeMap::from([("lib.rs".into(), "🚀café\r\n".into())]),
            item,
        )
    }

    #[test]
    fn actions_keep_exact_edits_and_withhold_whole_unsupported_actions() {
        let (root, sources, item) = fixture();
        let result = actions(&root, &sources, json!([item]), 1).unwrap();
        assert_eq!(result["actions"][0]["edits"][0]["start_byte"], 9);
        assert_eq!(result["actions"][0]["edits"][0]["replacement"], ": u32");
        assert_eq!(sources["lib.rs"], "🚀café\r\n");
        for (key, value, reason) in [
            (
                "command",
                json!({"command":"never.execute","arguments":["payload"]}),
                "command",
            ),
            ("disabled", json!({"reason":"missing context"}), "disabled"),
        ] {
            let mut blocked = item.clone();
            blocked[key] = value;
            let result = actions(&root, &sources, json!([blocked]), 1).unwrap();
            assert!(result["actions"][0]["edits"].is_null());
            assert!(
                result["actions"][0]["unsupported_reasons"]
                    .as_array()
                    .unwrap()
                    .contains(&json!(reason))
            );
        }
        let mut resource = item.clone();
        resource["edit"]["documentChanges"]
            .as_array_mut()
            .unwrap()
            .push(json!({"kind":"create","uri":"file:///new.rs"}));
        let result = actions(&root, &sources, json!([resource]), 1).unwrap();
        assert!(result["actions"][0]["edits"].is_null());
        assert_eq!(
            result["actions"][0]["unsupported_reasons"],
            json!(["resource_operation"])
        );
        let unresolved = json!({"title":"Resolve later","data":{"id":"opaque"}});
        let result = actions(&root, &sources, json!([unresolved]), 1).unwrap();
        assert!(result["actions"][0]["edits"].is_null());
        assert_eq!(
            actions(&root, &sources, Value::Null, 1).unwrap()["action_count"],
            0
        );
        // The legacy URI-to-edits representation is also accepted.
        let document = &item["edit"]["documentChanges"][0];
        let mapped = json!({"title":"Type","edit":{"changes":{
            (document["textDocument"]["uri"].as_str().unwrap()):document["edits"]}}});
        assert_eq!(
            actions(&root, &sources, json!([mapped]), 1).unwrap()["actions"][0]["text_edit_count"],
            1
        );
    }

    #[test]
    fn actions_reject_scope_versions_overlap_coordinates_and_budgets() {
        let (root, sources, item) = fixture();
        for uri in [
            "file:///outside.rs",
            "https://example.org/lib.rs",
            "file:///snapshot/../lib.rs",
        ] {
            let mut bad = item.clone();
            bad["edit"]["documentChanges"][0]["textDocument"]["uri"] = json!(uri);
            assert!(actions(&root, &sources, json!([bad]), 1).is_err());
        }
        for version in [json!(2), json!("1")] {
            let mut bad = item.clone();
            bad["edit"]["documentChanges"][0]["textDocument"]["version"] = version;
            assert!(actions(&root, &sources, json!([bad]), 1).is_err());
        }
        for (start, end) in [(1, 2), (6, 2), (0, 9)] {
            let mut bad = item.clone();
            bad["edit"]["documentChanges"][0]["edits"][0]["range"] =
                json!({"start":{"line":0,"character":start},"end":{"line":0,"character":end}});
            assert!(actions(&root, &sources, json!([bad]), 1).is_err());
        }
        let mut overlap = item.clone();
        let edit = &mut overlap["edit"]["documentChanges"][0]["edits"][0];
        edit["range"]["start"]["character"] = json!(2);
        let copy = edit.clone();
        overlap["edit"]["documentChanges"][0]["edits"]
            .as_array_mut()
            .unwrap()
            .push(copy);
        assert!(actions(&root, &sources, json!([overlap]), 1).is_err());
        assert!(actions(&root, &sources, json!([item, item]), 1).is_err());
        let mut large = item.clone();
        large["edit"]["documentChanges"][0]["edits"][0]["newText"] = json!("é".repeat(32_769));
        assert!(actions(&root, &sources, json!([large]), 1).is_err());
        for (key, value, expected) in [
            ("insertTextFormat", json!(2), "snippet"),
            ("annotationId", json!("a"), "annotations"),
        ] {
            let mut blocked = item.clone();
            blocked["edit"]["documentChanges"][0]["edits"][0][key] = value;
            let result = actions(&root, &sources, json!([blocked]), 1).unwrap();
            assert!(result["actions"][0]["edits"].is_null());
            assert_eq!(
                result["actions"][0]["unsupported_reasons"],
                json!([expected])
            );
        }
    }
}
