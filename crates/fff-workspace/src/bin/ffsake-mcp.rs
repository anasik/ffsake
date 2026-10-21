use fff_workspace::Workspace;
use fff_workspace::resident::{Request, Resident};
use serde_json::{Value, json};
use std::io::{self, BufRead, Write};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    if args.len() != 1 {
        eprintln!("usage: ffsake-mcp WORKSPACE");
        std::process::exit(2);
    }

    let mut runtime = Resident::open(Workspace::new(&args[0])?)?;
    let mut input = io::stdin().lock();
    let mut output = io::stdout().lock();

    while let Some(message) = read_message(&mut input)? {
        let response = match serde_json::from_slice::<Value>(&message) {
            Ok(message) => handle_message(&mut runtime, message),
            Err(error) => Some(json!({
                "jsonrpc":"2.0",
                "id":null,
                "error":{"code":-32700,"message":error.to_string()}
            })),
        };

        if let Some(response) = response {
            serde_json::to_writer(&mut output, &response)?;
            writeln!(output)?;
            output.flush()?;
        }
    }

    Ok(())
}

fn read_message(input: &mut impl BufRead) -> io::Result<Option<Vec<u8>>> {
    let mut bytes = Vec::new();
    let mut oversized = false;

    loop {
        let chunk = input.fill_buf()?;
        if chunk.is_empty() {
            return if bytes.is_empty() && !oversized {
                Ok(None)
            } else if oversized {
                Ok(Some(br#"{"__oversized":true}"#.to_vec()))
            } else {
                Ok(Some(bytes))
            };
        }

        let end = chunk.iter().position(|b| *b == b'\n');
        let used = end.map_or(chunk.len(), |n| n + 1);
        if bytes.len() + used <= 1024 * 1024 && !oversized {
            bytes.extend_from_slice(&chunk[..used]);
        } else {
            oversized = true;
        }
        input.consume(used);

        if end.is_some() {
            return if oversized {
                Ok(Some(br#"{"__oversized":true}"#.to_vec()))
            } else {
                Ok(Some(bytes))
            };
        }
    }
}

fn handle_message(runtime: &mut Resident, message: Value) -> Option<Value> {
    if message.get("__oversized") == Some(&Value::Bool(true)) {
        return Some(json!({
            "jsonrpc":"2.0","id":null,
            "error":{"code":-32600,"message":"request exceeds 1 MiB"}
        }));
    }

    let id = message.get("id")?.clone();
    let method = message.get("method").and_then(Value::as_str).unwrap_or_default();

    Some(match method {
        "initialize" => {
            json!({
                "jsonrpc":"2.0",
                "id":id,
                "result":{
                    "protocolVersion":"2025-11-25",
                    "capabilities":{"tools":{}},
                    "serverInfo":{"name":"ffsake","version":env!("CARGO_PKG_VERSION")}
                }
            })
        }
        "ping" => json!({"jsonrpc":"2.0","id":id,"result":{}}),
        "tools/list" => json!({
            "jsonrpc":"2.0",
            "id":id,
            "result":{"tools":tools()}
        }),
        "tools/call" => {
            let name = message
                .pointer("/params/name")
                .and_then(Value::as_str)
                .unwrap_or_default();
            let arguments = message
                .pointer("/params/arguments")
                .cloned()
                .unwrap_or_else(|| json!({}));

            let result = call_tool(runtime, name, arguments);
            let is_error = result.get("ok") == Some(&Value::Bool(false));
            json!({
                "jsonrpc":"2.0",
                "id":id,
                "result":{
                    "content":[{"type":"text","text":serde_json::to_string(&result).unwrap()}],
                    "isError":is_error
                }
            })
        }
        _ => json!({
            "jsonrpc":"2.0",
            "id":id,
            "error":{"code":-32601,"message":format!("method not found: {method}")}
        }),
    })
}

fn call_tool(runtime: &mut Resident, name: &str, arguments: Value) -> Value {
    let op = match name {
        "ffsake_read" => "read",
        "ffsake_list" => "list",
        "ffsake_target" => "target",
        "ffsake_search" => "search",
        "ffsake_preview_matches" => "preview_matches",
        "ffsake_apply" => "apply",
        "ffsake_undo" => "undo",
        "ffsake_redo" => "redo",
        "ffsake_release" => "release",
        "ffsake_refresh" => "refresh",
        _ => return json!({"ok":false,"error":format!("unknown tool: {name}")}),
    };

    let mut request = match arguments {
        Value::Object(map) => Value::Object(map),
        _ => return json!({"ok":false,"error":"tool arguments must be an object"}),
    };
    request["op"] = Value::String(op.to_string());

    let request = match serde_json::from_value::<Request>(request) {
        Ok(request) => request,
        Err(error) => return json!({"ok":false,"error":error.to_string()}),
    };

    match runtime.execute(request) {
        Ok(result) => json!({"ok":true,"result":result}),
        Err(error) => json!({"ok":false,"error":error.to_string()}),
    }
}

fn tools() -> Vec<Value> {
    vec![
        tool("ffsake_read", "Read a workspace file or line range.", json!({
            "type":"object","required":["path"],"properties":{
                "path":{"type":"string"},
                "start":{"type":"integer","minimum":1},
                "end":{"type":"integer","minimum":1}
            }
        })),
        tool("ffsake_list", "List a safe workspace-relative directory.", json!({
            "type":"object","required":["path"],"properties":{"path":{"type":"string"}}
        })),
        tool("ffsake_target", "Set the sticky workspace file target used by later searches.", json!({
            "type":"object","required":["target"],"properties":{"target":selector_schema()}
        })),
        tool("ffsake_search", "Search indexed workspace content and retain a server-owned MatchSet.", json!({
            "type":"object","required":["pattern"],"properties":{
                "pattern":{"type":"string"},
                "regex":{"type":"boolean"},
                "target":selector_schema(),
                "limit":{"type":"integer","minimum":1,"maximum":1000},
                "offset":{"type":"integer","minimum":0},
                "details":{"type":"boolean"}
            }
        })),
        tool("ffsake_preview_matches", "Preview replacing a cached MatchSet without writing.", json!({
            "type":"object","required":["handle","replacement","expected_matches"],"properties":{
                "handle":{"type":"integer","minimum":1},
                "replacement":{"type":"string"},
                "expected_matches":{"type":"integer","minimum":0}
            }
        })),
        tool("ffsake_apply", "Atomically apply a previously previewed transform.", json!({
            "type":"object","required":["handle"],"properties":{"handle":{"type":"integer","minimum":1}}
        })),
        tool("ffsake_undo", "Undo a journal transaction with state verification.", json!({
            "type":"object","required":["transaction"],"properties":{"transaction":{"type":"string"}}
        })),
        tool("ffsake_redo", "Redo a journal transaction with state verification.", json!({
            "type":"object","required":["transaction"],"properties":{"transaction":{"type":"string"}}
        })),
        tool("ffsake_release", "Release a resident MatchSet or preview handle.", json!({
            "type":"object","required":["handle"],"properties":{"handle":{"type":"integer","minimum":1}}
        })),
        tool("ffsake_refresh", "Wait for a full FFF index refresh.", json!({
            "type":"object","properties":{}
        })),
    ]
}

fn selector_schema() -> Value {
    json!({
        "type":"object",
        "required":["include","exclude"],
        "properties":{
            "include":{"type":"array","items":{}},
            "exclude":{"type":"array","items":{}}
        }
    })
}

fn tool(name: &str, description: &str, input_schema: Value) -> Value {
    json!({"name":name,"description":description,"inputSchema":input_schema})
}
