//! The same deliberately small, HTML-free Markdown subset as dashboard/lib/markdown.ts.
use fancy_regex::Regex;
use std::sync::LazyLock;
fn regex(pattern: &str) -> Regex {
    const SPACE: &str = r"[\x09-\x0d\x20\u{00a0}\u{1680}\u{2000}-\u{200a}\u{2028}\u{2029}\u{202f}\u{205f}\u{3000}\u{feff}]";
    const NON_SPACE: &str = r"[^\x09-\x0d\x20\u{00a0}\u{1680}\u{2000}-\u{200a}\u{2028}\u{2029}\u{202f}\u{205f}\u{3000}\u{feff}]";
    // INFALLIBLE: all callers supply the fixed patterns below, covered by parser tests.
    Regex::new(
        &pattern
            .replace(r"[\s\S]", r"(?s:.)")
            .replace(r"\s", SPACE)
            .replace(r"\S", NON_SPACE)
            .replace(r"\d", "[0-9]"),
    )
    .expect("fixed Markdown regular expression")
}
fn escape(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#39;")
}
fn inline(source: &str) -> String {
    static CODE: LazyLock<Regex> = LazyLock::new(|| regex(r"^`([^`\n]+)`"));
    static STRONG: LazyLock<Regex> = LazyLock::new(|| regex(r"^(\*\*|__)(?=\S)([\s\S]*?\S)\1"));
    static STAR: LazyLock<Regex> = LazyLock::new(|| regex(r"^\*(?=[^\s*])([^*\n]*?[^\s*])\*"));
    static UNDER: LazyLock<Regex> =
        LazyLock::new(|| regex(r"^_(?=[^\s_])([^_\n]*?[^\s_])_(?![A-Za-z0-9])"));
    static LINK: LazyLock<Regex> = LazyLock::new(|| regex(r"^\[([^\]\n]+)\]\(([^)\s]+)\)"));
    static SAFE: LazyLock<Regex> =
        LazyLock::new(|| regex(r"(?i)^(?:https?://|/#[a-z0-9][a-z0-9/?=&%_-]*$)"));
    let mut output = String::new();
    let mut index = 0;
    while index < source.len() {
        let rest = &source[index..];
        if let Ok(Some(m)) = CODE.captures(rest) {
            output.push_str(&format!(
                "<code class=\"rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]\">{}</code>",
                escape(&m[1])
            ));
            index += m.get(0).map(|m| m.end()).unwrap_or(0);
            continue;
        }
        if let Ok(Some(m)) = STRONG.captures(rest) {
            output.push_str(&format!(
                "<strong class=\"font-semibold text-foreground\">{}</strong>",
                inline(&m[2])
            ));
            index += m.get(0).map(|m| m.end()).unwrap_or(0);
            continue;
        }
        let m = STAR
            .captures(rest)
            .ok()
            .flatten()
            .or_else(|| UNDER.captures(rest).ok().flatten());
        if let Some(m) = m {
            let before = source[..index].chars().next_back();
            if rest.starts_with('*') || before.is_none_or(|c| !c.is_ascii_alphanumeric()) {
                output.push_str(&format!("<em>{}</em>", inline(&m[1])));
                index += m.get(0).map(|m| m.end()).unwrap_or(0);
                continue;
            }
        }
        if let Ok(Some(m)) = LINK.captures(rest) {
            if SAFE.is_match(&m[2]).unwrap_or(false) {
                let href = m[2]
                    .strip_prefix("/#")
                    .map(|v| format!("#{v}"))
                    .unwrap_or_else(|| m[2].to_string());
                output.push_str(&format!("<a href=\"{}\"{} class=\"underline underline-offset-2 hover:text-foreground\">{}</a>",escape(&href),if href.starts_with('#'){""}else{" target=\"_blank\" rel=\"noreferrer noopener\""},inline(&m[1])));
                index += m.get(0).map(|m| m.end()).unwrap_or(0);
                continue;
            }
        }
        if let Some(c) = rest.chars().next() {
            output.push_str(&escape(&c.to_string()));
            index += c.len_utf8();
        } else {
            break;
        }
    }
    output
}
pub fn render(source: &str) -> String {
    static HEADING: LazyLock<Regex> = LazyLock::new(|| regex(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$"));
    static BULLET: LazyLock<Regex> =
        LazyLock::new(|| regex(r"^\s{0,3}([-*+]|\d{1,9}[.)])\s+(.*)$"));
    static FENCE: LazyLock<Regex> = LazyLock::new(|| regex(r"^\s*```"));
    static CONTINUATION: LazyLock<Regex> = LazyLock::new(|| regex(r"^\s{2,}\S"));
    let normalized = source.replace("\r\n", "\n").replace('\r', "\n");
    let lines: Vec<_> = normalized.split('\n').collect();
    let mut output = String::new();
    let mut paragraph = Vec::<&str>::new();
    let mut index = 0;
    let flush = |paragraph: &mut Vec<&str>, output: &mut String| {
        if !paragraph.is_empty() {
            output.push_str(&format!(
                "<p class=\"my-2 whitespace-pre-wrap first:mt-0 last:mb-0\">{}</p>",
                inline(&paragraph.join("\n"))
            ));
            paragraph.clear();
        }
    };
    while index < lines.len() {
        let line = lines[index];
        if FENCE.is_match(line).unwrap_or(false) {
            flush(&mut paragraph, &mut output);
            let mut body = Vec::new();
            index += 1;
            while index < lines.len() && !FENCE.is_match(lines[index]).unwrap_or(false) {
                body.push(lines[index]);
                index += 1;
            }
            output.push_str(&format!("<pre class=\"my-2 overflow-x-auto rounded-lg border bg-muted/60 p-3 font-mono text-xs leading-5\"><code>{}</code></pre>",escape(&body.join("\n"))));
            index += 1;
            continue;
        }
        if let Ok(Some(m)) = HEADING.captures(line) {
            flush(&mut paragraph, &mut output);
            let level = m[1].len().min(3);
            let class = if level == 1 {
                "mt-4 mb-1.5 text-base font-semibold first:mt-0"
            } else {
                "mt-4 mb-1 text-[15px] font-semibold first:mt-0"
            };
            output.push_str(&format!(
                "<h{} class=\"{class}\">{}</h{}>",
                level + 2,
                inline(&m[2]),
                level + 2
            ));
            index += 1;
            continue;
        }
        if let Ok(Some(m)) = BULLET.captures(line) {
            flush(&mut paragraph, &mut output);
            let ordered = m[1].bytes().any(|c| c.is_ascii_digit());
            let start = m[1].trim_end_matches(['.', ')']);
            let mut current = m[2].to_string();
            let mut items = Vec::new();
            index += 1;
            while index < lines.len() {
                if let Ok(Some(next)) = BULLET.captures(lines[index]) {
                    if next[1].bytes().any(|c| c.is_ascii_digit()) == ordered {
                        items.push(inline(&current));
                        current = next[2].to_string();
                        index += 1;
                        continue;
                    }
                    break;
                }
                if CONTINUATION.is_match(lines[index]).unwrap_or(false) {
                    current.push('\n');
                    current.push_str(lines[index].trim());
                    index += 1;
                    continue;
                }
                break;
            }
            items.push(inline(&current));
            let tag = if ordered { "ol" } else { "ul" };
            output.push_str(&format!(
                "<{tag}{} class=\"my-2 {} space-y-1 pl-5 first:mt-0 last:mb-0\">",
                if ordered {
                    format!(" start=\"{start}\"")
                } else {
                    String::new()
                },
                if ordered { "list-decimal" } else { "list-disc" }
            ));
            for item in items {
                output.push_str(&format!("<li class=\"pl-1 whitespace-pre-wrap marker:text-muted-foreground\">{item}</li>"));
            }
            output.push_str(&format!("</{tag}>"));
            continue;
        }
        if line.trim().is_empty() {
            flush(&mut paragraph, &mut output)
        } else {
            paragraph.push(line)
        }
        index += 1;
    }
    flush(&mut paragraph, &mut output);
    output
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn escapes_html_and_unsafe_links() {
        let text = render(
            "<script>alert(1)</script>\n\n**bold** [bad](javascript:alert) [good](https://example.com)",
        );
        assert!(text.contains("&lt;script&gt;"));
        assert!(!text.contains("href=\"javascript:"));
        assert!(text.contains("<strong"));
        assert!(text.contains("href=\"https://example.com\""));
    }
    #[test]
    fn lists_code_and_headers() {
        let out = render("# Heading\n\n1. One\n2. Two\n\n```\n<code>\n```");
        assert!(out.contains("<h3"));
        assert!(out.contains("<ol start=\"1\""));
        assert!(out.contains("&lt;code&gt;"));
    }
}
