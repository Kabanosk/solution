mod data;

use data::{monthly_payroll, snapshot, Period, EMPLOYEES};
use std::sync::atomic::{AtomicUsize, Ordering};
use yew::prelude::*;

/// A shared namespace also keeps repeated cards, rows and SVG paths unique.
fn next_html_id(tag: &str) -> String {
    static NEXT_ID: AtomicUsize = AtomicUsize::new(1);
    format!("lantern-{tag}-{}", NEXT_ID.fetch_add(1, Ordering::Relaxed))
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Page {
    Overview,
    Staffing,
    Investigation,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum IconName {
    Activity,
    Alert,
    Bank,
    Bell,
    Check,
    ChevronDown,
    Clock,
    Dashboard,
    Document,
    Lock,
    Search,
    Slack,
    Spark,
    Team,
    Wallet,
}

#[derive(Properties, PartialEq)]
struct IconProps {
    name: IconName,
    #[prop_or(18)]
    size: u16,
}

#[function_component(Icon)]
fn icon(props: &IconProps) -> Html {
    let contents = match props.name {
        IconName::Activity => {
            html! {<><path id={next_html_id("path")} d="M3 12h4l2.5-7 5 14 2.5-7h4"/></>}
        }
        IconName::Alert => {
            html! {<><path id={next_html_id("path")} d="M10.3 3.4 2.5 17a2 2 0 0 0 1.7 3h15.6a2 2 0 0 0 1.7-3L13.7 3.4a2 2 0 0 0-3.4 0Z"/><path id={next_html_id("path")} d="M12 9v4"/><path id={next_html_id("path")} d="M12 17h.01"/></>}
        }
        IconName::Bank => {
            html! {<><path id={next_html_id("path")} d="m3 10 9-6 9 6"/><path id={next_html_id("path")} d="M5 10v8"/><path id={next_html_id("path")} d="M9 10v8"/><path id={next_html_id("path")} d="M15 10v8"/><path id={next_html_id("path")} d="M19 10v8"/><path id={next_html_id("path")} d="M3 20h18"/></>}
        }
        IconName::Bell => {
            html! {<><path id={next_html_id("path")} d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path id={next_html_id("path")} d="M10 21h4"/></>}
        }
        IconName::Check => html! {<path id={next_html_id("path")} d="m5 12 4 4L19 6"/>},
        IconName::ChevronDown => html! {<path id={next_html_id("path")} d="m6 9 6 6 6-6"/>},
        IconName::Clock => {
            html! {<><circle id={next_html_id("circle")} cx="12" cy="12" r="9"/><path id={next_html_id("path")} d="M12 7v5l3 2"/></>}
        }
        IconName::Dashboard => {
            html! {<><rect id={next_html_id("rect")} x="3" y="3" width="7" height="7" rx="1"/><rect id={next_html_id("rect")} x="14" y="3" width="7" height="7" rx="1"/><rect id={next_html_id("rect")} x="3" y="14" width="7" height="7" rx="1"/><rect id={next_html_id("rect")} x="14" y="14" width="7" height="7" rx="1"/></>}
        }
        IconName::Document => {
            html! {<><path id={next_html_id("path")} d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path id={next_html_id("path")} d="M14 2v6h6"/><path id={next_html_id("path")} d="M8 13h8"/><path id={next_html_id("path")} d="M8 17h5"/></>}
        }
        IconName::Lock => {
            html! {<><rect id={next_html_id("rect")} x="4" y="10" width="16" height="11" rx="2"/><path id={next_html_id("path")} d="M8 10V7a4 4 0 0 1 8 0v3"/></>}
        }
        IconName::Search => {
            html! {<><circle id={next_html_id("circle")} cx="11" cy="11" r="7"/><path id={next_html_id("path")} d="m20 20-4-4"/></>}
        }
        IconName::Slack => {
            html! {<><path id={next_html_id("path")} d="M7 14a2 2 0 1 1-2-2h2v2Z"/><path id={next_html_id("path")} d="M9 14a2 2 0 1 1 4 0v5a2 2 0 1 1-4 0v-5Z"/><path id={next_html_id("path")} d="M10 7a2 2 0 1 1 2-2v2h-2Z"/><path id={next_html_id("path")} d="M10 9a2 2 0 1 1 0 4H5a2 2 0 1 1 0-4h5Z"/><path id={next_html_id("path")} d="M17 10a2 2 0 1 1 2 2h-2v-2Z"/><path id={next_html_id("path")} d="M15 10a2 2 0 1 1-4 0V5a2 2 0 1 1 4 0v5Z"/><path id={next_html_id("path")} d="M14 17a2 2 0 1 1-2 2v-2h2Z"/><path id={next_html_id("path")} d="M14 15a2 2 0 1 1 0-4h5a2 2 0 1 1 0 4h-5Z"/></>}
        }
        IconName::Spark => {
            html! {<><path id={next_html_id("path")} d="m12 3-1.2 3.3A4 4 0 0 1 8.3 8.8L5 10l3.3 1.2a4 4 0 0 1 2.5 2.5L12 17l1.2-3.3a4 4 0 0 1 2.5-2.5L19 10l-3.3-1.2a4 4 0 0 1-2.5-2.5L12 3Z"/><path id={next_html_id("path")} d="m5 3-.5 1.5L3 5l1.5.5L5 7l.5-1.5L7 5l-1.5-.5L5 3Z"/></>}
        }
        IconName::Team => {
            html! {<><path id={next_html_id("path")} d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle id={next_html_id("circle")} cx="9" cy="7" r="4"/><path id={next_html_id("path")} d="M22 21v-2a4 4 0 0 0-3-3.9"/><path id={next_html_id("path")} d="M16 3.1a4 4 0 0 1 0 7.8"/></>}
        }
        IconName::Wallet => {
            html! {<><path id={next_html_id("path")} d="M20 7V5a2 2 0 0 0-2-2H5a3 3 0 0 0 0 6h15v12H5a3 3 0 0 1-3-3V6"/><path id={next_html_id("path")} d="M16 13h4"/></>}
        }
    };

    html! {
        <svg id={next_html_id("svg")}
            aria-hidden="true"
            width={props.size.to_string()}
            height={props.size.to_string()}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
            stroke-linecap="round"
            stroke-linejoin="round"
        >
            {contents}
        </svg>
    }
}

#[function_component(App)]
fn app() -> Html {
    let page = use_state(|| Page::Overview);
    let main_ref = use_node_ref();
    use_effect(|| {
        // Trunk injects head assets outside Yew. Include these elements in the
        // same ID convention without replacing meaningful application IDs.
        if let Some(document) = web_sys::window().and_then(|window| window.document()) {
            let elements = document.get_elements_by_tag_name("*");
            for index in 0..elements.length() {
                if let Some(element) = elements.item(index) {
                    if element.id().is_empty() {
                        element.set_id(&next_html_id(&element.tag_name().to_lowercase()));
                    }
                }
            }
        }
    });
    {
        let main_ref = main_ref.clone();
        use_effect_with(*page, move |_| {
            if let Some(main) = main_ref.cast::<web_sys::HtmlElement>() {
                let _ = main.focus();
            }
        });
    }
    let first_alert_resolved = use_state(|| false);
    let dm_requested = use_state(|| false);
    let invoice_received = use_state(|| false);
    let action_notice = use_state(|| None::<&'static str>);

    let set_overview = {
        let page = page.clone();
        Callback::from(move |_| page.set(Page::Overview))
    };
    let set_staffing = {
        let page = page.clone();
        Callback::from(move |_| page.set(Page::Staffing))
    };
    let set_investigation = {
        let page = page.clone();
        Callback::from(move |_| page.set(Page::Investigation))
    };
    let notify_action = {
        let action_notice = action_notice.clone();
        Callback::from(move |message: &'static str| action_notice.set(Some(message)))
    };

    html! {
        <div id={next_html_id("div")} class="app-shell dark" data-aesir-theme="mist">
            <Sidebar
                current={*page}
                set_overview={set_overview.clone()}
                set_staffing={set_staffing.clone()}
                set_investigation={set_investigation.clone()}
            />
            <div id={next_html_id("div")} class="workspace">
                <Topbar />
                <main id="main-content" ref={main_ref} tabindex="-1" aria-label={match *page { Page::Overview => "Overview", Page::Staffing => "Staffing and payroll", Page::Investigation => "Invoice investigation" }}>
                    if *page == Page::Overview {
                        <Overview
                            first_alert_resolved={*first_alert_resolved}
                            open_staffing={set_staffing.clone()}
                            notify_action={notify_action.clone()}
                            open_investigation={set_investigation.clone()}
                        />
                    } else if *page == Page::Staffing {
                        <Staffing
                            back_to_overview={set_overview.clone()}
                            notify_action={notify_action.clone()}
                        />
                    } else {
                        <Investigation
                            resolved={*first_alert_resolved}
                            requested={dm_requested.clone()}
                            received={invoice_received.clone()}
                            resolve={Callback::from({
                                let first_alert_resolved = first_alert_resolved.clone();
                                move |_| first_alert_resolved.set(true)
                            })}
                        />
                    }
                </main>
                if let Some(message) = *action_notice {
                    <div id={next_html_id("div")} class="action-toast" role="status">
                        <span id={next_html_id("span")} class="toast-icon"><Icon name={IconName::Check} size={15} /></span>
                        <span id={next_html_id("span")}>{message}</span>
                        <button id={next_html_id("button")}
                            class="icon-button"
                            aria-label="Dismiss notification"
                            onclick={{
                                let action_notice = action_notice.clone();
                                Callback::from(move |_| action_notice.set(None))
                            }}
                        >{"×"}</button>
                    </div>
                }
            </div>
        </div>
    }
}

#[derive(Properties, PartialEq)]
struct SidebarProps {
    current: Page,
    set_overview: Callback<MouseEvent>,
    set_staffing: Callback<MouseEvent>,
    set_investigation: Callback<MouseEvent>,
}

#[function_component(Sidebar)]
fn sidebar(props: &SidebarProps) -> Html {
    let companies_open = use_state(|| false);
    let company_button_ref = use_node_ref();
    let company_option_ref = use_node_ref();
    {
        let company_option_ref = company_option_ref.clone();
        use_effect_with(*companies_open, move |open| {
            if *open {
                if let Some(option) = company_option_ref.cast::<web_sys::HtmlElement>() {
                    let _ = option.focus();
                }
            }
        });
    }
    let close_companies = {
        let companies_open = companies_open.clone();
        let company_button_ref = company_button_ref.clone();
        Callback::from(move |_| {
            companies_open.set(false);
            if let Some(button) = company_button_ref.cast::<web_sys::HtmlElement>() {
                let _ = button.focus();
            }
        })
    };
    html! {
        <aside id={next_html_id("aside")} class="sidebar" aria-label="Primary navigation">
            <div id={next_html_id("div")} class="brand-block">
                <div id={next_html_id("div")} class="brand-mark" aria-hidden="true"><Icon name={IconName::Spark} size={19} /></div>
                <div id={next_html_id("div")}>
                    <div id={next_html_id("div")} class="brand-name">{"Lantern"}</div>
                    <div id={next_html_id("div")} class="brand-meta">{"Autonomous CFO"}</div>
                </div>
            </div>

            <button id="lantern-company-switcher" ref={company_button_ref.clone()} class="company-switcher" aria-label="Select company" aria-haspopup="listbox" aria-expanded={(*companies_open).to_string()} aria-controls="lantern-company-list" onclick={{let companies_open = companies_open.clone(); Callback::from(move |_| companies_open.set(!*companies_open))}}>
                <span id={next_html_id("span")} class="company-avatar">{"PA"}</span>
                <span id={next_html_id("span")} class="company-copy">
                    <strong id="lantern-company-name">{"Demo Bistro"}</strong>
                    <small id={next_html_id("small")}>{"Demo city · EUR"}</small>
                </span>
                <Icon name={IconName::ChevronDown} size={16} />
            </button>
            if *companies_open {
                <div id="lantern-company-list" class="aesir-card" role="listbox" aria-label="Companies" onkeydown={{let close_companies = close_companies.clone(); Callback::from(move |event: KeyboardEvent| { if event.key() == "Escape" { event.prevent_default(); close_companies.emit(()); } })}}>
                    <button id="lantern-company-demo-bistro" ref={company_option_ref} class="nav-item is-active" role="option" aria-selected="true" onclick={{let close_companies = close_companies.clone(); Callback::from(move |_| close_companies.emit(()))}}><span id="lantern-company-selected-mark"><Icon name={IconName::Check} size={16} /></span><span id="lantern-company-option-label">{"Demo Bistro"}</span></button>
                </div>
            }

            <nav id={next_html_id("nav")} class="sidebar-nav">
                <span id={next_html_id("span")} class="nav-label">{"Workspace"}</span>
                <button id={next_html_id("button")}
                    class={classes!("nav-item", (props.current == Page::Overview).then_some("is-active"))}
                    aria-current={(props.current == Page::Overview).then_some("page")}
                    onclick={props.set_overview.clone()}
                >
                    <Icon name={IconName::Dashboard} />
                    <span id={next_html_id("span")}>{"Overview"}</span>
                </button>
                <button id={next_html_id("button")}
                    class={classes!("nav-item", (props.current == Page::Staffing).then_some("is-active"))}
                    aria-current={(props.current == Page::Staffing).then_some("page")}
                    onclick={props.set_staffing.clone()}
                >
                    <Icon name={IconName::Team} />
                    <span id={next_html_id("span")}>{"Staffing"}</span>
                </button>
                <button id={next_html_id("button")} class={classes!("nav-item", (props.current == Page::Investigation).then_some("is-active"))}
                    aria-current={(props.current == Page::Investigation).then_some("page")}
                    onclick={props.set_investigation.clone()}>
                    <Icon name={IconName::Document} />
                    <span id={next_html_id("span")}>{"Investigations"}</span>
                </button>
                <button id={next_html_id("button")} class="nav-item nav-item-muted" aria-label="Connections, coming soon" disabled=true>
                    <Icon name={IconName::Activity} />
                    <span id={next_html_id("span")}>{"Connections"}</span>
                </button>
            </nav>

            <div id={next_html_id("div")} class="sidebar-spacer" />
            <div id={next_html_id("div")} class="agent-card">
                <div id={next_html_id("div")} class="agent-card-head">
                    <span id={next_html_id("span")} class="live-pulse" aria-hidden="true" />
                    <strong id={next_html_id("strong")}>{"Demo workspace"}</strong>
                </div>
                <p id={next_html_id("p")}>{"Explore the autonomous CFO loop"}</p>
                <div id={next_html_id("div")} class="agent-systems" aria-label="Connected systems">
                    <span id={next_html_id("span")}><Icon name={IconName::Bank} size={14} /></span>
                    <span id={next_html_id("span")}><Icon name={IconName::Slack} size={14} /></span>
                    <span id={next_html_id("span")}><Icon name={IconName::Document} size={14} /></span>
                    <span id={next_html_id("span")}><Icon name={IconName::Lock} size={14} /></span>
                </div>
            </div>

            <div id={next_html_id("div")} class="user-block">
                <span id={next_html_id("span")} class="aesir-avatar avatar-green" role="img" aria-label="Wojtek Kowalski">{"WK"}</span>
                <span id={next_html_id("span")} class="user-copy"><strong id={next_html_id("strong")}>{"Wojtek Kowalski"}</strong><small id={next_html_id("small")}>{"Administrator"}</small></span>
                <button id={next_html_id("button")} class="icon-button" aria-label="Open account menu"><Icon name={IconName::ChevronDown} size={16} /></button>
            </div>
        </aside>
    }
}

#[function_component(Topbar)]
fn topbar() -> Html {
    html! {
        <header id={next_html_id("header")} class="topbar">
            <a id={next_html_id("a")} class="skip-link" href="#main-content">{"Skip to content"}</a>
            <div id={next_html_id("div")} class="breadcrumb"><span id={next_html_id("span")}>{"Finance workspace"}</span><span id={next_html_id("span")} aria-hidden="true">{"/"}</span><strong id={next_html_id("strong")}>{"2025 close"}</strong></div>
            <label id={next_html_id("label")} class="search-box">
                <span id={next_html_id("span")} class="sr-only">{"Search dashboard"}</span>
                <Icon name={IconName::Search} size={17} />
                <input id={next_html_id("input")} type="search" placeholder="Search transactions, people…" />
            </label>
            <div id={next_html_id("div")} class="topbar-actions">
                <span id={next_html_id("span")} class="sync-status"><Icon name={IconName::Spark} size={14} />{"Demo · Sample integrations"}</span>
                <button id={next_html_id("button")} class="icon-button notification-button" aria-label="Notifications, 1 unread">
                    <Icon name={IconName::Bell} size={18} />
                    <span id={next_html_id("span")} class="notification-dot" aria-hidden="true" />
                </button>
            </div>
        </header>
    }
}

#[derive(Properties, PartialEq)]
struct OverviewProps {
    first_alert_resolved: bool,
    open_staffing: Callback<MouseEvent>,
    notify_action: Callback<&'static str>,
    open_investigation: Callback<MouseEvent>,
}

#[function_component(Overview)]
fn overview(props: &OverviewProps) -> Html {
    let year_data = snapshot(Period::Year);

    html! {
        <div id={next_html_id("div")} class="page-shell">
            <section id={next_html_id("section")} class="page-heading">
                <div id={next_html_id("div")}>
                    <p id={next_html_id("p")} class="eyebrow">{"Your autonomous finance workspace"}</p>
                    <h1 id={next_html_id("h1")}>{"Good morning, Wojtek"}</h1>
                    <p id={next_html_id("p")}>{format!("{} bank transactions. {} open items. Every decision connected to its evidence.", format_count(year_data.transactions), if props.first_alert_resolved { 2 } else { 3 })}</p>
                </div>
                <button id={next_html_id("button")}
                    class="aesir-button"
                    data-variant="accent"
                    data-size="default"
                    onclick={props.open_investigation.clone()}
                >
                    <Icon name={IconName::Spark} size={16} />
                    {"Explore an investigation"}
                </button>
            </section>

            <section id={next_html_id("section")} class="metric-grid" aria-label="Financial summary">
                <MetricCard
                    label="Available balance"
                    value="€42,000.00"
                    note="Closing balance · 31 Dec 2025"
                    trend="Recovered from June low"
                    icon={IconName::Wallet}
                    tone="positive"
                />
                <MetricCard
                    label="Money in"
                    value={format_compact(year_data.total_revenue)}
                    note="Card settlements · 2025"
                    trend="Synthetic demo transactions"
                    icon={IconName::Bank}
                    tone="neutral"
                />
                <MetricCard
                    label="Money out"
                    value={format_compact(year_data.total_expenses)}
                    note="Operating outflow · 2025"
                    trend="6 categories tracked"
                    icon={IconName::Activity}
                    tone="neutral"
                />
                <MetricCard
                    label="Needs attention"
                    value={if props.first_alert_resolved { "2" } else { "3" }}
                    note="Unmatched or unusual items"
                    trend={if props.first_alert_resolved { "1 resolved just now" } else { "1 high priority" }}
                    icon={IconName::Alert}
                    tone="warning"
                />
            </section>

            <section id="lantern-financial-charts" class="aesir-card staff-card" aria-labelledby="lantern-financial-charts-title">
                <div id="lantern-financial-charts-heading" class="aesir-card__header card-title-row">
                    <div id="lantern-financial-charts-copy"><h2 id="lantern-financial-charts-title" class="aesir-card__title">{"Financial outlook"}</h2><p id="lantern-financial-charts-subtitle" class="aesir-card__description">{"Demo Bistro · Main account · EUR"}</p></div>
                    <span id="lantern-financial-charts-source" class="aesir-badge" data-tone="warning">{"Forecast uses mock data"}</span>
                </div>
                <div id="lantern-financial-chart-content" class="aesir-card__content">
                    <canvas id="lantern-cash-forecast-canvas" tabindex="0" aria-label="Interactive financial charts. Select cash balance forecast or expense mix using the tabs inside the viewer. Dates are on X, cash balance is on Y. Focus chart and use Control plus or Control minus to zoom." style="display:block;width:100%;height:500px;">
                        {"Your browser needs WebGL to display these charts. Cash forecast is illustrative; expense totals are from the synthetic 2025 demo statement."}
                    </canvas>
                </div>
                <div id="lantern-financial-charts-footer" class="aesir-card__footer">
                    <p id="lantern-forecast-status" class="aesir-card__description" role="status">{"Loading financial charts…"}</p>
                    <details id="lantern-chart-data"><summary id="lantern-chart-data-toggle">{"Chart data and controls"}</summary>
                        <p id="lantern-chart-controls" class="aesir-card__description">{"Use the viewer’s tabs or keys 1/2 to select a plot. Cash forecast: date on X, balance (EUR) on Y. Focus the canvas and press Ctrl+ or Ctrl− to zoom; drag to pan. Keys Y/Q select the expense period. The forecast is a mock scenario seeded from the 2025 closing balance, not a current bank projection."}</p>
                        <p id="lantern-chart-expense-source" class="aesir-card__description">{"Expense mix: select Year or Q4 in the viewer. Values are calculated from the synthetic 2025 demo statement."}</p>
                        {for [Period::Year, Period::QuarterFour].iter().map(|period| {
                            let data = snapshot(*period);
                            html! {<p id={next_html_id("p")} class="aesir-card__description">{format!("{}: {}", if *period == Period::Year { "Year" } else { "Q4" }, data.expenses.iter().map(|category| format!("{} {} ({:.0}%)", category.name, format_currency(category.amount), category.percentage)).collect::<Vec<_>>().join("; "))}</p>}
                        })}
                    </details>
                </div>
            </section>

            <div id={next_html_id("div")}>
                <section id={next_html_id("section")} class="aesir-card staff-card priority-card" aria-labelledby="priority-title">
                    <div id={next_html_id("div")} class="aesir-card__header card-title-row">
                        <div id={next_html_id("div")}>
                            <h2 id="priority-title" class="aesir-card__title">{"Priority inbox"}</h2>
                            <p id={next_html_id("p")} class="aesir-card__description">{"Evidence gathered by the autonomous finance loop"}</p>
                        </div>
                        <span id={next_html_id("span")} class="aesir-badge" data-tone="warning">{if props.first_alert_resolved { "2 open" } else { "3 open" }}</span>
                    </div>
                    <div id={next_html_id("div")} class="aesir-card__content alert-list">
                        if !props.first_alert_resolved {
                            <article id={next_html_id("article")} class="alert-row alert-critical">
                                <span id={next_html_id("span")} class="alert-icon"><Icon name={IconName::Document} size={17} /></span>
                                <div id={next_html_id("div")} class="alert-copy">
                                    <div id={next_html_id("div")} class="alert-meta"><span id={next_html_id("span")} class="aesir-badge" data-tone="critical">{"Missing invoice"}</span><span id={next_html_id("span")}>{"31 Dec · 09:42"}</span></div>
                                    <h3 id={next_html_id("h3")}>{"€320.00 payment has no matching document"}</h3>
                                    <p id={next_html_id("p")}>{"Demo Supply Co. · Girocard purchase · checked files and Slack history"}</p>
                                </div>
                                <button id={next_html_id("button")} class="aesir-button" data-variant="outline" data-size="sm" onclick={props.open_investigation.clone()}>{"Investigate"}</button>
                            </article>
                        } else {
                            <div id={next_html_id("div")} class="resolved-banner" role="status"><Icon name={IconName::Check} size={16} /><span id={next_html_id("span")}><strong id={next_html_id("strong")}>{"Invoice matched."}</strong>{" Evidence recorded in this demo session."}</span></div>
                        }
                        <article id={next_html_id("article")} class="alert-row">
                            <span id={next_html_id("span")} class="alert-icon tone-orange"><Icon name={IconName::Activity} size={17} /></span>
                            <div id={next_html_id("div")} class="alert-copy">
                                <div id={next_html_id("div")} class="alert-meta"><span id={next_html_id("span")} class="aesir-badge" data-tone="warning">{"Liquidity"}</span><span id={next_html_id("span")}>{"Forecast"}</span></div>
                                <h3 id={next_html_id("h3")}>{"June runway dropped below safety threshold"}</h3>
                                <p id={next_html_id("p")}>{"A fictional three-week closure caused a temporary −€12,000.00 overdraft. Buffer rule suggested."}</p>
                            </div>
                            <button id={next_html_id("button")}
                                class="aesir-button"
                                data-variant="ghost"
                                data-size="sm"
                                onclick={{
                                    let notify = props.notify_action.clone();
                                    Callback::from(move |_| notify.emit("June closed at −€12,000.00 after a three-week closure. Consider a cash reserve covering one month of fixed costs."))
                                }}
                            >{"View insight"}</button>
                        </article>
                        <article id={next_html_id("article")} class="alert-row">
                            <span id={next_html_id("span")} class="alert-icon tone-blue"><Icon name={IconName::Lock} size={17} /></span>
                            <div id={next_html_id("div")} class="alert-copy">
                                <div id={next_html_id("div")} class="alert-meta"><span id={next_html_id("span")} class="aesir-badge" data-tone="info">{"Access review"}</span><span id={next_html_id("span")}>{"Sample policy"}</span></div>
                                <h3 id={next_html_id("h3")}>{"Confirm the finance team’s evidence scope"}</h3>
                                <p id={next_html_id("p")}>{"Approved invoice folders only. Private executive files stay outside the finance role."}</p>
                            </div>
                            <button id={next_html_id("button")}
                                class="aesir-button"
                                data-variant="ghost"
                                data-size="sm"
                                onclick={props.open_investigation.clone()}
                            >{"Review policy"}</button>
                        </article>
                    </div>
                </section>


            </div>

            <section id={next_html_id("section")} class="lower-grid">
                <div id={next_html_id("div")} class="aesir-card connection-card">
                    <div id={next_html_id("div")} class="aesir-card__header card-title-row">
                        <div id={next_html_id("div")}><h2 id={next_html_id("h2")} class="aesir-card__title">{"Finance loop"}</h2><p id={next_html_id("p")} class="aesir-card__description">{"Continuous evidence chain"}</p></div>
                        <span id={next_html_id("span")} class="aesir-badge" data-tone="info">{"Simulated"}</span>
                    </div>
                    <div id={next_html_id("div")} class="aesir-card__content connection-flow">
                        <ConnectionStep icon={IconName::Bank} title="Bank feed" detail="Revolut mock" status="Imported" />
                        <span id={next_html_id("span")} class="flow-line" aria-hidden="true" />
                        <ConnectionStep icon={IconName::Document} title="Files & invoices" detail="Employee devices" status="Scoped" />
                        <span id={next_html_id("span")} class="flow-line" aria-hidden="true" />
                        <ConnectionStep icon={IconName::Slack} title="Team context" detail="4 owners" status="Connected" />
                        <span id={next_html_id("span")} class="flow-line" aria-hidden="true" />
                        <ConnectionStep icon={IconName::Lock} title="IAM guard" detail="Least privilege" status="Enforced" />
                    </div>
                </div>
                <div id={next_html_id("div")} class="aesir-card payroll-card">
                    <div id={next_html_id("div")} class="aesir-card__header card-title-row">
                        <div id={next_html_id("div")}><h2 id={next_html_id("h2")} class="aesir-card__title">{"Next payroll"}</h2><p id={next_html_id("p")} class="aesir-card__description">{"Scheduled for 28 November"}</p></div>
                        <Icon name={IconName::Clock} size={18} />
                    </div>
                    <div id={next_html_id("div")} class="aesir-card__content payroll-preview">
                        <div id={next_html_id("div")}><span id={next_html_id("span")}>{"4 people"}</span><strong id={next_html_id("strong")}>{format_currency(monthly_payroll())}</strong></div>
                        <div id={next_html_id("div")} class="avatar-stack" aria-label="Payroll recipients">
                            {for EMPLOYEES.iter().map(|employee| html! {<span id={next_html_id("span")} class={classes!("aesir-avatar", "avatar-sm", employee.gradient)} role="img" aria-label={employee.name}>{employee.initials}</span>})}
                        </div>
                        <button id={next_html_id("button")} class="aesir-button" data-variant="secondary" data-size="sm" onclick={props.open_staffing.clone()}>{"Review staffing"}</button>
                    </div>
                </div>
            </section>
        </div>
    }
}

#[derive(Properties, PartialEq)]
struct InvestigationProps {
    resolved: bool,
    requested: UseStateHandle<bool>,
    received: UseStateHandle<bool>,
    resolve: Callback<MouseEvent>,
}

#[function_component(Investigation)]
fn investigation(props: &InvestigationProps) -> Html {
    let evidence_tab = use_state(|| 0_u8);
    let requested = props.requested.clone();
    let received = props.received.clone();
    let intern = use_state(|| false);
    let question = use_state(|| 0_u8);
    let answers = [
        "I found an outgoing €320.00 payment to Demo Supply Co.. There is no matching invoice in the sample invoicing system or Taylor’s approved finance folder. The next step is to ask Taylor in Slack.",
        "I checked the bank reference, invoice registry, Taylor’s finance folder and the permitted purchasing conversation. No cancellation or refund was found. Each source is listed in the evidence panel.",
        "Your role controls which evidence I can return. An intern cannot access CEO files or private CEO messages. A request through Slack follows the same permissions as a request here.",
    ];
    html! {
        <div id={next_html_id("div")} class="page-shell">
            <section id={next_html_id("section")} class="page-heading">
                <div id={next_html_id("div")}><p id={next_html_id("p")} class="eyebrow">{"INVESTIGATIONS / INV-0042"}</p><h1 id={next_html_id("h1")}>{"Follow the money. Find the context."}</h1><p id={next_html_id("p")}>{"One payment, every source, a clear next step."}</p></div>
                <span id={next_html_id("span")} class="aesir-badge" data-tone={if props.resolved { "success" } else { "warning" }}>{if props.resolved { "Resolved · invoice matched" } else { "Missing invoice" }}</span>
            </section>
            <section id={next_html_id("section")} class="aesir-card staff-card" aria-label="Demo and transaction details">
                <div id={next_html_id("div")} class="aesir-card__header"><p id={next_html_id("p")} class="aesir-card__description">{"Interactive demo · Bank, device access and Slack actions are simulated. No messages are sent."}</p></div>
                <div id={next_html_id("div")} class="aesir-card__content payroll-preview">
                    <div id={next_html_id("div")}><span id={next_html_id("span")}>{"OUTGOING TRANSFER · REVOLUT MOCK"}</span><h2 id={next_html_id("h2")} class="aesir-card__title">{"Demo Supply Co."}</h2><p id={next_html_id("p")} class="aesir-card__description">{"31 Dec 2025 · Girocard purchase · Owner: Taylor"}</p></div>
                    <div id={next_html_id("div")}><strong id={next_html_id("strong")}>{"−€320.00"}</strong><span id={next_html_id("span")}>{"Invoice required"}</span></div>
                </div>
            </section>
            <div id={next_html_id("div")} class="dashboard-grid">
                <div id={next_html_id("div")}>
                    <section id={next_html_id("section")} class="aesir-card staff-card">
                        <div id={next_html_id("div")} class="aesir-card__header"><h2 id={next_html_id("h2")} class="aesir-card__title">{"Lantern’s investigation"}</h2><p id={next_html_id("p")} class="aesir-card__description">{"Initial checks and the next action"}</p></div>
                        <div id={next_html_id("div")} class="aesir-card__content alert-list">
                            {for [
                                (IconName::Bank, "Payment detected", "Outgoing €320.00 transfer imported from the Revolut mock. Merchant and reference DEMO-32000 identified.", "Found"),
                                (IconName::Document, "Invoice registry checked", "No invoice matched the amount, merchant or payment reference during the initial check.", "No match"),
                                (IconName::Search, "Owner’s company laptop checked", "No matching document in TAYLOR-LT / Finance / Invoices. Only the approved folder was searched over the VPN.", "No file"),
                                (IconName::Slack, "Team context reviewed", "The approved #purchasing conversation confirmed the order. No cancellation or refund was found.", "Checked"),
                            ].iter().map(|(icon, title, text, status)| html! {
                                <article id={next_html_id("article")} class="alert-row"><span id={next_html_id("span")} class="alert-icon"><Icon name={*icon} size={17} /></span><div id={next_html_id("div")} class="alert-copy"><h3 id={next_html_id("h3")}>{*title}</h3><p id={next_html_id("p")}>{*text}</p><span id={next_html_id("span")} class="aesir-badge" data-tone="neutral">{*status}</span></div></article>
                            })}
                            <article id={next_html_id("article")} class="alert-row"><span id={next_html_id("span")} class="alert-icon"><Icon name={if props.resolved { IconName::Check } else { IconName::Team }} size={17} /></span><div id={next_html_id("div")} class="alert-copy"><h3 id={next_html_id("h3")}>{if props.resolved { "Resolved with supporting evidence" } else if *received { "Invoice received from Taylor" } else if *requested { "Waiting for Taylor" } else { "Ask the person who knows" }}</h3><p id={next_html_id("p")}>{if props.resolved { "Sample invoice DEMO-2025-001 matched the payment. No bank transfer was initiated." } else if *received { "Invoice DEMO-2025-001 · €320.00 · Demo Supply Co.. Ready to match." } else if *requested { "A simulated Slack request is recorded in the conversation panel." } else { "Taylor owns this payment. Request the missing invoice in Slack." }}</p></div></article>
                        </div>
                        <div id={next_html_id("div")} class="aesir-card__footer"><p id={next_html_id("p")} class="aesir-card__description">{"Each source check follows the requesting user’s permissions."}</p></div>
                    </section>
                    <section id={next_html_id("section")} class="aesir-card staff-card">
                        <div id={next_html_id("div")} class="aesir-card__header"><h2 id={next_html_id("h2")} class="aesir-card__title">{"Source evidence"}</h2><p id={next_html_id("p")} class="aesir-card__description">{"Original checks · Preserved for the audit trail"}</p></div>
                        <div id={next_html_id("div")} class="aesir-card__content">
                            <div id={next_html_id("div")} class="segmented-control" role="group" aria-label="Evidence sources">
                                {for ["Bank record", "Device files", "Slack context"].iter().enumerate().map(|(i, label)| {
                                    let tab = evidence_tab.clone();
                                    html! {<button id={format!("source-tab-{i}")} aria-pressed={(*evidence_tab == i as u8).to_string()} aria-controls="source-panel" class={classes!((*evidence_tab == i as u8).then_some("is-active"))} onclick={Callback::from(move |_| tab.set(i as u8))}>{*label}</button>}
                                })}
                            </div>
                            <div id="source-panel" role="region" aria-labelledby={format!("source-tab-{}", *evidence_tab)} tabindex="0">
                                if *evidence_tab == 0 {
                                    <p id={next_html_id("p")} class="aesir-card__description">{"Reference: DEMO-32000"}</p><p id={next_html_id("p")} class="aesir-card__description">{"Counterparty: Demo Supply Co."}</p><p id={next_html_id("p")} class="aesir-card__description">{"Amount: €320.00 EUR · Outgoing · Sample transaction"}</p>
                                } else if *evidence_tab == 1 {
                                    <p id={next_html_id("p")} class="aesir-card__description">{"No matching invoice found in TAYLOR-LT / Finance / Invoices."}</p><p id={next_html_id("p")} class="aesir-card__description">{"Search limited to the approved finance folder. Personal files excluded."}</p>
                                } else {
                                    <p id={next_html_id("p")} class="aesir-card__description">{"Sample · #purchasing · Taylor · 31 Dec, 09:12"}</p><p id={next_html_id("p")} class="aesir-card__description">{"“Picked up the supplies from Demo Supply Co.. Paid by company card; I’ll send the receipt after delivery.”"}</p><p id={next_html_id("p")} class="aesir-card__description">{"Original finding · Order confirmed, supporting invoice missing"}</p>
                                }
                            </div>
                        </div>
                    </section>
                </div>
                <div id={next_html_id("div")}>
                    <section id={next_html_id("section")} class="aesir-card staff-card">
                        <div id={next_html_id("div")} class="aesir-card__header"><h2 id={next_html_id("h2")} class="aesir-card__title">{"Bring the owner into the loop"}</h2><p id={next_html_id("p")} class="aesir-card__description">{"Slack DM · Taylor · Invoicing"}</p></div>
                        <div id={next_html_id("div")} class="aesir-card__content" aria-live="polite">
                            <div id={next_html_id("div")} class="alert-copy"><h3 id={next_html_id("h3")}>{"Lantern"}</h3><span id={next_html_id("span")} class="aesir-badge" data-tone="neutral">{if *requested || props.resolved { "Demo message" } else { "Message preview" }}</span><p id={next_html_id("p")}>{"Hi Taylor, I found a €320.00 payment to Demo Supply Co. on 31 Dec. I couldn’t find a matching invoice in the invoicing system or your finance folder. Could you share it here, or let me know if the order was cancelled?"}</p><p id={next_html_id("p")}>{"DEMO-32000 · €320.00 · Demo Supply Co."}</p></div>
                            if *received || props.resolved {
                                <div id={next_html_id("div")} class="alert-copy"><h3 id={next_html_id("h3")}>{"Taylor · Sample reply"}</h3><p id={next_html_id("p")}>{"Here’s the invoice. Thanks for catching it!"}</p><p id={next_html_id("p")}>{"DEMO-2025-001.pdf · €320.00 · Sample document"}</p></div>
                            }
                        </div>
                        <div id={next_html_id("div")} class="aesir-card__footer">
                            if props.resolved {
                                <div id={next_html_id("div")} class="resolved-banner" role="status"><Icon name={IconName::Check} size={17} />{"Invoice matched · Case resolved"}</div>
                            } else if *received {
                                <button id={next_html_id("button")} class="aesir-button" data-variant="accent" data-size="default" onclick={props.resolve.clone()}>{"Match invoice & resolve"}</button>
                            } else if *requested {
                                <p id={next_html_id("p")} class="aesir-card__description" role="status">{"Demo request recorded. Waiting for the owner."}</p><button id={next_html_id("button")} class="aesir-button" data-variant="outline" data-size="default" onclick={{let received = received.clone(); Callback::from(move |_| received.set(true))}}>{"Simulate Taylor’s reply"}</button>
                            } else {
                                <button id={next_html_id("button")} class="aesir-button" data-variant="accent" data-size="default" onclick={{let requested = requested.clone(); Callback::from(move |_| requested.set(true))}}>{"Send demo request"}</button>
                            }
                        </div>
                    </section>
                    <section id={next_html_id("section")} class="aesir-card staff-card">
                        <div id={next_html_id("div")} class="aesir-card__header"><h2 id={next_html_id("h2")} class="aesir-card__title">{"Access follows the person"}</h2><p id={next_html_id("p")} class="aesir-card__description">{"IAM policy preview · Same rules in Slack"}</p></div>
                        <div id={next_html_id("div")} class="aesir-card__content">
                            <div id={next_html_id("div")} class="segmented-control" aria-label="Preview user role">
                                <button id={next_html_id("button")} aria-pressed={(!*intern).to_string()} class={classes!((!*intern).then_some("is-active"))} onclick={{let intern = intern.clone(); Callback::from(move |_| intern.set(false))}}>{"Finance lead"}</button>
                                <button id={next_html_id("button")} aria-pressed={(*intern).to_string()} class={classes!((*intern).then_some("is-active"))} onclick={{let intern = intern.clone(); Callback::from(move |_| intern.set(true))}}>{"Intern"}</button>
                            </div>
                            <p id={next_html_id("p")} class="policy-row staff-footer"><span id={next_html_id("span")}>{"Finance invoices"}</span><span id={next_html_id("span")} class="aesir-badge" data-tone={if *intern { "warning" } else { "success" }}>{if *intern { "Denied" } else { "Read access" }}</span></p>
                            <p id={next_html_id("p")} class="policy-row staff-footer"><span id={next_html_id("span")}>{"CEO’s private files"}</span><span id={next_html_id("span")} class="aesir-badge" data-tone="warning">{"Denied"}</span></p>
                            <p id={next_html_id("p")} class="aesir-card__description" aria-live="polite">{if *intern { "Intern policy: only assigned records. Asking Lantern does not grant access to restricted documents." } else { "Finance policy: approved invoice folders and purchasing context. CEO private data requires a separate permission." }}</p>
                        </div>
                    </section>
                    <section id={next_html_id("section")} class="aesir-card staff-card" aria-labelledby="ask-title">
                        <div id={next_html_id("div")} class="aesir-card__header"><h2 id="ask-title" class="aesir-card__title">{"Ask Lantern about this case"}</h2><p id={next_html_id("p")} class="aesir-card__description">{"Explore sample answers, here or in Slack."}</p></div>
                        <div id={next_html_id("div")} class="aesir-card__content cfo-answers"><div id={next_html_id("div")} class="alert-list">{for ["What’s missing?", "What did you check?", "Who can access this?"].iter().enumerate().map(|(i, label)| {
                            let question = question.clone();
                            html! {<button id={next_html_id("button")} class="aesir-button" data-variant={if *question == i as u8 { "secondary" } else { "outline" }} data-size="sm" aria-pressed={(*question == i as u8).to_string()} onclick={Callback::from(move |_| question.set(i as u8))}>{*label}</button>}
                        })}</div><p id={next_html_id("p")} class="aesir-card__description" aria-live="polite">{if *question == 0 && props.resolved { "This case is resolved. Taylor supplied sample invoice DEMO-2025-001 for €320.00, matched to the outgoing payment. Original evidence is preserved." } else if *question == 0 && *received { "Invoice DEMO-2025-001 for €320.00 is ready for review. Choose Match invoice & resolve to complete this case." } else if *question == 0 && *requested { "The invoice is still missing. A demo request is recorded for Taylor; the case stays open until evidence arrives." } else { answers[*question as usize] }}</p></div>
                    </section>
                </div>
            </div>
        </div>
    }
}

#[derive(Properties, PartialEq)]
struct MetricCardProps {
    label: &'static str,
    value: AttrValue,
    note: &'static str,
    trend: &'static str,
    icon: IconName,
    tone: &'static str,
}

#[function_component(MetricCard)]
fn metric_card(props: &MetricCardProps) -> Html {
    html! {
        <article id={next_html_id("article")} class="metric-card">
            <div id={next_html_id("div")} class={classes!("metric-icon", format!("tone-{}", props.tone))}><Icon name={props.icon} size={18} /></div>
            <p id={next_html_id("p")}>{props.label}</p>
            <strong id={next_html_id("strong")} class="metric-value">{props.value.clone()}</strong>
            <span id={next_html_id("span")} class="metric-note">{props.note}</span>
            <span id={next_html_id("span")} class={classes!("metric-trend", format!("trend-{}", props.tone))}>{props.trend}</span>
        </article>
    }
}

#[derive(Properties, PartialEq)]
struct ConnectionStepProps {
    icon: IconName,
    title: &'static str,
    detail: &'static str,
    status: &'static str,
}

#[function_component(ConnectionStep)]
fn connection_step(props: &ConnectionStepProps) -> Html {
    html! {
        <div id={next_html_id("div")} class="connection-step">
            <span id={next_html_id("span")} class="connection-icon"><Icon name={props.icon} size={17} /></span>
            <div id={next_html_id("div")}><strong id={next_html_id("strong")}>{props.title}</strong><small id={next_html_id("small")}>{props.detail}</small></div>
            <span id={next_html_id("span")} class="connection-status"><Icon name={IconName::Check} size={12} />{props.status}</span>
        </div>
    }
}

#[derive(Properties, PartialEq)]
struct StaffingProps {
    back_to_overview: Callback<MouseEvent>,
    notify_action: Callback<&'static str>,
}

#[function_component(Staffing)]
fn staffing(props: &StaffingProps) -> Html {
    html! {
        <div id={next_html_id("div")} class="page-shell">
            <section id={next_html_id("section")} class="page-heading staffing-heading">
                <div id={next_html_id("div")}>
                    <p id={next_html_id("p")} class="eyebrow">{"People operations"}</p>
                    <h1 id={next_html_id("h1")}>{"Staffing & payroll"}</h1>
                    <p id={next_html_id("p")}>{"A current view of people, responsibilities and recurring salary commitments."}</p>
                </div>
                <button id={next_html_id("button")} class="aesir-button" data-variant="outline" data-size="default" onclick={props.back_to_overview.clone()}>{"Back to overview"}</button>
            </section>

            <section id={next_html_id("section")} class="staffing-summary" aria-label="Staffing summary">
                <div id={next_html_id("div")}><span id={next_html_id("span")}>{"Active team"}</span><strong id={next_html_id("strong")}>{"4"}</strong><small id={next_html_id("small")}>{"All payroll profiles verified"}</small></div>
                <div id={next_html_id("div")}><span id={next_html_id("span")}>{"Monthly net payroll"}</span><strong id={next_html_id("strong")}>{format_currency(monthly_payroll())}</strong><small id={next_html_id("small")}>{"Paid on the 28th"}</small></div>
                <div id={next_html_id("div")}><span id={next_html_id("span")}>{"Employer contributions"}</span><strong id={next_html_id("strong")}>{"€2,800.00"}</strong><small id={next_html_id("small")}>{"Social security · monthly"}</small></div>
                <div id={next_html_id("div")}><span id={next_html_id("span")}>{"Next payroll"}</span><strong id={next_html_id("strong")}>{"8 days"}</strong><small id={next_html_id("small")}>{"28 November 2026"}</small></div>
            </section>

            <section id={next_html_id("section")} class="aesir-card staff-card" aria-labelledby="team-title">
                <div id={next_html_id("div")} class="aesir-card__header card-title-row staff-card-heading">
                    <div id={next_html_id("div")}><h2 id="team-title" class="aesir-card__title">{"Team directory"}</h2><p id={next_html_id("p")} class="aesir-card__description">{"Salary data from the synthetic Demo Bistro scenario"}</p></div>
                    <div id={next_html_id("div")} class="staff-actions">
                        <label id={next_html_id("label")} class="small-search"><Icon name={IconName::Search} size={15} /><span id={next_html_id("span")} class="sr-only">{"Search team"}</span><input id={next_html_id("input")} type="search" placeholder="Search people" /></label>
                        <button id={next_html_id("button")}
                            class="aesir-button"
                            data-variant="secondary"
                            data-size="sm"
                            onclick={{
                                let notify = props.notify_action.clone();
                                Callback::from(move |_| notify.emit("Payroll export prepared in demo mode."))
                            }}
                        >{"Export payroll"}</button>
                    </div>
                </div>
                <div id={next_html_id("div")} class="staff-table" role="table" aria-label="Employee salaries">
                    <div id={next_html_id("div")} class="staff-table-header" role="row">
                        <span id={next_html_id("span")} role="columnheader">{"Employee"}</span><span id={next_html_id("span")} role="columnheader">{"Team"}</span><span id={next_html_id("span")} role="columnheader">{"Monthly net"}</span><span id={next_html_id("span")} role="columnheader">{"Status"}</span>
                    </div>
                    {for EMPLOYEES.iter().map(|employee| html! {
                        <div id={next_html_id("div")} class="staff-row" role="row">
                            <div id={next_html_id("div")} class="employee-cell" role="cell">
                                <span id={next_html_id("span")} class={classes!("aesir-avatar", "avatar-lg", employee.gradient)} role="img" aria-label={employee.name}>{employee.initials}</span>
                                <span id={next_html_id("span")}><strong id={next_html_id("strong")}>{employee.name}</strong><small id={next_html_id("small")}>{employee.role}</small></span>
                            </div>
                            <span id={next_html_id("span")} class="team-cell" role="cell"><span id={next_html_id("span")} class="team-icon"><Icon name={if employee.team == "Leadership" { IconName::Spark } else { IconName::Team }} size={14} /></span>{employee.team}</span>
                            <span id={next_html_id("span")} class="salary-cell" role="cell"><strong id={next_html_id("strong")}>{format_currency(employee.salary)}</strong><small id={next_html_id("small")}>{"€ · monthly"}</small></span>
                            <span id={next_html_id("span")} role="cell"><span id={next_html_id("span")} class="aesir-badge" data-tone="success"><span id={next_html_id("span")} class="status-dot" />{"Active"}</span></span>
                        </div>
                    })}
                </div>
                <div id={next_html_id("div")} class="aesir-card__footer staff-footer">
                    <span id={next_html_id("span")}><Icon name={IconName::Lock} size={14} />{"Salary access follows IAM role policy"}</span>
                    <span id={next_html_id("span")}>{"Last verified 2 minutes ago"}</span>
                </div>
            </section>

            <section id={next_html_id("section")} class="staff-insight">
                <div id={next_html_id("div")} class="insight-icon"><Icon name={IconName::Spark} size={20} /></div>
                <div id={next_html_id("div")}><strong id={next_html_id("strong")}>{"CFO insight"}</strong><p id={next_html_id("p")}>{"Payroll represents a stable €9,200 monthly commitment. The June closure kept all salaries running, which explains 76.7% of the temporary liquidity gap."}</p></div>
                <button id={next_html_id("button")}
                    class="aesir-button"
                    data-variant="ghost"
                    data-size="sm"
                    onclick={{
                        let notify = props.notify_action.clone();
                        Callback::from(move |_| notify.emit("Payroll analysis selected. Detailed scenario modeling is queued for integration."))
                    }}
                >{"Open analysis"}</button>
            </section>
        </div>
    }
}

fn format_currency(value: f64) -> String {
    let rounded = (value * 100.0).round() as i64;
    let euros = rounded / 100;
    let cents = (rounded % 100).abs();
    let digits = euros.abs().to_string();
    let mut grouped = String::new();
    for (index, character) in digits.chars().enumerate() {
        if index > 0 && (digits.len() - index) % 3 == 0 {
            grouped.push(',');
        }
        grouped.push(character);
    }
    let sign = if rounded < 0 { "−" } else { "" };
    format!("{sign}€{grouped}.{cents:02}")
}

fn format_compact(value: f64) -> AttrValue {
    if value >= 1_000_000.0 {
        format!("€{:.2}M", value / 1_000_000.0).into()
    } else if value >= 1_000.0 {
        format!("€{:.1}k", value / 1_000.0).into()
    } else {
        format_currency(value).into()
    }
}

fn format_count(value: usize) -> String {
    let digits = value.to_string();
    let mut grouped = String::new();
    for (index, character) in digits.chars().enumerate() {
        if index > 0 && (digits.len() - index) % 3 == 0 {
            grouped.push(',');
        }
        grouped.push(character);
    }
    grouped
}

fn main() {
    yew::Renderer::<App>::new().render();
}
