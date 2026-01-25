//! # Hitlog Viewer CLI
//!
//! Command-line tool for querying and analyzing enforcement hitlogs.
//!
//! Usage:
//!   hitlog_viewer recent [--limit N]
//!   hitlog_viewer blocked [--limit N]
//!   hitlog_viewer by-agent <agent_id> [--limit N]
//!   hitlog_viewer by-session <session_id>
//!   hitlog_viewer stats
//!   hitlog_viewer tail [-f]

use clap::{Parser, Subcommand};
use std::path::PathBuf;
use tupl_dp::telemetry::{HitlogQuery, QueryFilter};

#[derive(Parser)]
#[command(name = "hitlog_viewer")]
#[command(about = "Query and analyze Tupl enforcement hitlogs", long_about = None)]
struct Cli {
    /// Path to hitlog directory
    #[arg(short, long, default_value = "/var/hitlogs")]
    dir: PathBuf,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Show recent enforcement sessions
    Recent {
        /// Maximum number of sessions to show
        #[arg(short, long, default_value_t = 10)]
        limit: usize,
    },

    /// Show only blocked sessions
    Blocked {
        /// Maximum number of sessions to show
        #[arg(short, long)]
        limit: Option<usize>,
    },

    /// Show sessions for a specific agent
    ByAgent {
        /// Agent ID to filter by
        agent_id: String,

        /// Maximum number of sessions to show
        #[arg(short, long)]
        limit: Option<usize>,
    },

    /// Show a specific session by ID
    BySession {
        /// Session ID
        session_id: String,
    },

    /// Show aggregate statistics
    Stats,

    /// Query with custom filters
    Query {
        /// Layer filter (L0-L6)
        #[arg(long)]
        layer: Option<String>,

        /// Agent ID filter
        #[arg(long)]
        agent_id: Option<String>,

        /// Decision filter (0=BLOCK, 1=ALLOW)
        #[arg(long)]
        decision: Option<u8>,

        /// Start timestamp (Unix ms)
        #[arg(long)]
        start_time: Option<u64>,

        /// End timestamp (Unix ms)
        #[arg(long)]
        end_time: Option<u64>,

        /// Rule ID that was evaluated
        #[arg(long)]
        rule_id: Option<String>,

        /// Maximum results
        #[arg(short, long, default_value_t = 100)]
        limit: usize,

        /// Output format: json, pretty, summary
        #[arg(short, long, default_value = "pretty")]
        format: String,
    },
}

fn main() -> Result<(), String> {
    let cli = Cli::parse();

    let query = HitlogQuery::new(&cli.dir);

    match cli.command {
        Commands::Recent { limit } => {
            println!("📋 Recent {} enforcement sessions:\n", limit);
            let sessions = query.recent(limit)?;

            for session in sessions {
                print_session_summary(&session);
            }
        }

        Commands::Blocked { limit } => {
            println!("🚫 Blocked sessions:\n");
            let sessions = query.blocked(limit)?;

            for session in sessions {
                print_session_summary(&session);
            }
        }

        Commands::ByAgent { agent_id, limit } => {
            println!("🤖 Sessions for agent '{}':\n", agent_id);
            let sessions = query.by_agent(agent_id, limit)?;

            for session in sessions {
                print_session_summary(&session);
            }
        }

        Commands::BySession { session_id } => {
            let filter = QueryFilter {
                session_id: Some(session_id.clone()),
                limit: Some(1),
                ..Default::default()
            };

            let result = query.query(&filter)?;

            if let Some(session) = result.sessions.first() {
                print_session_detail(session);
            } else {
                println!("❌ Session '{}' not found", session_id);
            }
        }

        Commands::Stats => {
            let stats = query.statistics()?;

            println!("📊 Hitlog Statistics\n");
            println!("Total Sessions:  {}", stats.total_sessions);
            println!(
                "Blocked:         {} ({:.1}%)",
                stats.blocked,
                stats.block_rate * 100.0
            );
            println!(
                "Allowed:         {} ({:.1}%)",
                stats.allowed,
                (1.0 - stats.block_rate) * 100.0
            );
            println!("Avg Duration:    {} μs", stats.avg_duration_us);
            println!("Avg Rules/Session: {:.1}", stats.avg_rules_per_session);
        }

        Commands::Query {
            layer,
            agent_id,
            decision,
            start_time,
            end_time,
            rule_id,
            limit,
            format,
        } => {
            let filter = QueryFilter {
                layer,
                agent_id,
                decision,
                start_time_ms: start_time,
                end_time_ms: end_time,
                rule_id,
                limit: Some(limit),
                ..Default::default()
            };

            let result = query.query(&filter)?;

            println!("🔍 Query Results: {} matches\n", result.total_matched);

            match format.as_str() {
                "json" => {
                    println!(
                        "{}",
                        serde_json::to_string_pretty(&result.sessions).unwrap()
                    );
                }
                "summary" => {
                    for session in &result.sessions {
                        println!(
                            "{} | {} | {} | {} rules | {} μs",
                            session.session_id,
                            if session.final_decision == 0 {
                                "BLOCK"
                            } else {
                                "ALLOW"
                            },
                            session.layer,
                            session.rules_evaluated.len(),
                            session.duration_us
                        );
                    }
                }
                _ => {
                    for session in &result.sessions {
                        print_session_summary(session);
                    }
                }
            }
        }
    }

    Ok(())
}

fn print_session_summary(session: &tupl_dp::telemetry::session::EnforcementSession) {
    let decision_icon = if session.final_decision == 0 {
        "🚫"
    } else {
        "✅"
    };
    let decision_text = if session.final_decision == 0 {
        "BLOCK"
    } else {
        "ALLOW"
    };

    println!(
        "{} {} | {} | {} | {} rules | {} μs",
        decision_icon,
        session.session_id[..8].to_string(),
        decision_text,
        session.layer,
        session.rules_evaluated.len(),
        session.duration_us
    );

    // Show which rule caused the block
    if session.final_decision == 0 {
        if let Some(blocking_rule) = session.rules_evaluated.iter().find(|r| r.decision == 0) {
            println!(
                "   ↳ Blocked by: {} ({})",
                blocking_rule.rule_id, blocking_rule.rule_family
            );
        }
    }

    println!();
}

fn print_session_detail(session: &tupl_dp::telemetry::session::EnforcementSession) {
    println!("═══════════════════════════════════════════════════════════");
    println!("Session Details");
    println!("═══════════════════════════════════════════════════════════\n");

    println!("Session ID:  {}", session.session_id);
    println!("Timestamp:   {} (Unix ms)", session.timestamp_ms);
    println!("Layer:       {}", session.layer);
    println!(
        "Decision:    {} {}",
        if session.final_decision == 0 {
            "🚫 BLOCK"
        } else {
            "✅ ALLOW"
        },
        if session.final_decision == 0 { "" } else { "" }
    );
    println!("Duration:    {} μs", session.duration_us);

    if let Some(ref agent_id) = session.agent_id {
        println!("Agent ID:    {}", agent_id);
    }

    println!("\n───────────────────────────────────────────────────────────");
    println!("Intent");
    println!("───────────────────────────────────────────────────────────\n");

    println!("{}", session.intent_json);

    println!("\n───────────────────────────────────────────────────────────");
    println!("Rules Evaluated ({})", session.rules_evaluated.len());
    println!("───────────────────────────────────────────────────────────\n");

    for (i, rule_eval) in session.rules_evaluated.iter().enumerate() {
        let decision_icon = if rule_eval.decision == 0 {
            "🚫"
        } else {
            "✅"
        };

        println!(
            "{}. {} {} (priority: {})",
            i + 1,
            decision_icon,
            rule_eval.rule_id,
            rule_eval.priority
        );
        println!("   Family:       {}", rule_eval.rule_family);
        println!("   Duration:     {} μs", rule_eval.duration_us);
        println!(
            "   Similarities: action={:.2}, resource={:.2}, data={:.2}, risk={:.2}",
            rule_eval.slice_similarities[0],
            rule_eval.slice_similarities[1],
            rule_eval.slice_similarities[2],
            rule_eval.slice_similarities[3]
        );
        println!(
            "   Thresholds:   action={:.2}, resource={:.2}, data={:.2}, risk={:.2}",
            rule_eval.thresholds[0],
            rule_eval.thresholds[1],
            rule_eval.thresholds[2],
            rule_eval.thresholds[3]
        );

        if rule_eval.short_circuited {
            println!("   ⚡ SHORT-CIRCUITED (stopped further evaluation)");
        }

        println!();
    }

    println!("───────────────────────────────────────────────────────────");
    println!("Performance");
    println!("───────────────────────────────────────────────────────────\n");

    println!(
        "Encoding:     {} μs",
        session.performance.encoding_duration_us
    );
    println!(
        "Rule Query:   {} μs",
        session.performance.rule_query_duration_us
    );
    println!(
        "Evaluation:   {} μs",
        session.performance.evaluation_duration_us
    );
    println!("Total:        {} μs", session.performance.total_duration_us);

    if session.performance.short_circuited {
        println!(
            "Short-circuit: YES (saved {} rule evaluations)",
            session.performance.rules_queried - session.performance.rules_evaluated
        );
    }

    println!("\n═══════════════════════════════════════════════════════════\n");
}
