mod data;

use data::{monthly_payroll, snapshot, ExpenseCategory, Period, EMPLOYEES};
use yew::prelude::*;

#[derive(Clone, Copy, PartialEq, Eq)]
enum Page {
    Overview,
    Staffing,
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
        IconName::Activity => html! {<><path d="M3 12h4l2.5-7 5 14 2.5-7h4"/></>},
        IconName::Alert => {
            html! {<><path d="M10.3 3.4 2.5 17a2 2 0 0 0 1.7 3h15.6a2 2 0 0 0 1.7-3L13.7 3.4a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4"/><path d="M12 17h.01"/></>}
        }
        IconName::Bank => {
            html! {<><path d="m3 10 9-6 9 6"/><path d="M5 10v8"/><path d="M9 10v8"/><path d="M15 10v8"/><path d="M19 10v8"/><path d="M3 20h18"/></>}
        }
        IconName::Bell => {
            html! {<><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 21h4"/></>}
        }
        IconName::Check => html! {<path d="m5 12 4 4L19 6"/>},
        IconName::ChevronDown => html! {<path d="m6 9 6 6 6-6"/>},
        IconName::Clock => html! {<><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>},
        IconName::Dashboard => {
            html! {<><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></>}
        }
        IconName::Document => {
            html! {<><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6"/><path d="M8 13h8"/><path d="M8 17h5"/></>}
        }
        IconName::Lock => {
            html! {<><rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></>}
        }
        IconName::Search => html! {<><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></>},
        IconName::Slack => {
            html! {<><path d="M7 14a2 2 0 1 1-2-2h2v2Z"/><path d="M9 14a2 2 0 1 1 4 0v5a2 2 0 1 1-4 0v-5Z"/><path d="M10 7a2 2 0 1 1 2-2v2h-2Z"/><path d="M10 9a2 2 0 1 1 0 4H5a2 2 0 1 1 0-4h5Z"/><path d="M17 10a2 2 0 1 1 2 2h-2v-2Z"/><path d="M15 10a2 2 0 1 1-4 0V5a2 2 0 1 1 4 0v5Z"/><path d="M14 17a2 2 0 1 1-2 2v-2h2Z"/><path d="M14 15a2 2 0 1 1 0-4h5a2 2 0 1 1 0 4h-5Z"/></>}
        }
        IconName::Spark => {
            html! {<><path d="m12 3-1.2 3.3A4 4 0 0 1 8.3 8.8L5 10l3.3 1.2a4 4 0 0 1 2.5 2.5L12 17l1.2-3.3a4 4 0 0 1 2.5-2.5L19 10l-3.3-1.2a4 4 0 0 1-2.5-2.5L12 3Z"/><path d="m5 3-.5 1.5L3 5l1.5.5L5 7l.5-1.5L7 5l-1.5-.5L5 3Z"/></>}
        }
        IconName::Team => {
            html! {<><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.9"/><path d="M16 3.1a4 4 0 0 1 0 7.8"/></>}
        }
        IconName::Wallet => {
            html! {<><path d="M20 7V5a2 2 0 0 0-2-2H5a3 3 0 0 0 0 6h15v12H5a3 3 0 0 1-3-3V6"/><path d="M16 13h4"/></>}
        }
    };

    html! {
        <svg
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
    let period = use_state(|| Period::Year);
    let first_alert_resolved = use_state(|| false);
    let action_notice = use_state(|| None::<&'static str>);

    let set_overview = {
        let page = page.clone();
        Callback::from(move |_| page.set(Page::Overview))
    };
    let set_staffing = {
        let page = page.clone();
        Callback::from(move |_| page.set(Page::Staffing))
    };
    let notify_action = {
        let action_notice = action_notice.clone();
        Callback::from(move |message: &'static str| action_notice.set(Some(message)))
    };

    html! {
        <div class="app-shell dark" data-aesir-theme="mist">
            <Sidebar
                current={*page}
                set_overview={set_overview.clone()}
                set_staffing={set_staffing.clone()}
            />
            <div class="workspace">
                <Topbar />
                <main id="main-content" tabindex="-1">
                    if *page == Page::Overview {
                        <Overview
                            period={*period}
                            set_period={Callback::from({
                                let period = period.clone();
                                move |next| period.set(next)
                            })}
                            first_alert_resolved={*first_alert_resolved}
                            resolve_alert={Callback::from({
                                let first_alert_resolved = first_alert_resolved.clone();
                                move |_| first_alert_resolved.set(true)
                            })}
                            open_staffing={set_staffing.clone()}
                            notify_action={notify_action.clone()}
                        />
                    } else {
                        <Staffing
                            back_to_overview={set_overview.clone()}
                            notify_action={notify_action.clone()}
                        />
                    }
                </main>
                if let Some(message) = *action_notice {
                    <div class="action-toast" role="status">
                        <span class="toast-icon"><Icon name={IconName::Check} size={15} /></span>
                        <span>{message}</span>
                        <button
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
}

#[function_component(Sidebar)]
fn sidebar(props: &SidebarProps) -> Html {
    html! {
        <aside class="sidebar" aria-label="Primary navigation">
            <div class="brand-block">
                <div class="brand-mark" aria-hidden="true"><Icon name={IconName::Spark} size={19} /></div>
                <div>
                    <div class="brand-name">{"LedgerLoop"}</div>
                    <div class="brand-meta">{"Autonomous CFO"}</div>
                </div>
            </div>

            <div class="company-switcher" aria-label="Active company">
                <span class="company-avatar">{"PA"}</span>
                <span class="company-copy">
                    <strong>{"Demo Bistro"}</strong>
                    <small>{"Demo city · EUR"}</small>
                </span>
                <Icon name={IconName::ChevronDown} size={16} />
            </div>

            <nav class="sidebar-nav">
                <span class="nav-label">{"Workspace"}</span>
                <button
                    class={classes!("nav-item", (props.current == Page::Overview).then_some("is-active"))}
                    aria-current={(props.current == Page::Overview).then_some("page")}
                    onclick={props.set_overview.clone()}
                >
                    <Icon name={IconName::Dashboard} />
                    <span>{"Overview"}</span>
                </button>
                <button
                    class={classes!("nav-item", (props.current == Page::Staffing).then_some("is-active"))}
                    aria-current={(props.current == Page::Staffing).then_some("page")}
                    onclick={props.set_staffing.clone()}
                >
                    <Icon name={IconName::Team} />
                    <span>{"Staffing"}</span>
                </button>
                <button class="nav-item nav-item-muted" aria-label="Documents, coming soon" disabled=true>
                    <Icon name={IconName::Document} />
                    <span>{"Documents"}</span>
                    <span class="nav-count">{"3"}</span>
                </button>
                <button class="nav-item nav-item-muted" aria-label="Connections, coming soon" disabled=true>
                    <Icon name={IconName::Activity} />
                    <span>{"Connections"}</span>
                </button>
            </nav>

            <div class="sidebar-spacer" />
            <div class="agent-card">
                <div class="agent-card-head">
                    <span class="live-pulse" aria-hidden="true" />
                    <strong>{"Agent is running"}</strong>
                </div>
                <p>{"Monitoring 4 connected systems"}</p>
                <div class="agent-systems" aria-label="Connected systems">
                    <span><Icon name={IconName::Bank} size={14} /></span>
                    <span><Icon name={IconName::Slack} size={14} /></span>
                    <span><Icon name={IconName::Document} size={14} /></span>
                    <span><Icon name={IconName::Lock} size={14} /></span>
                </div>
            </div>

            <div class="user-block">
                <span class="aesir-avatar avatar-green" role="img" aria-label="Wojtek Kowalski">{"WK"}</span>
                <span class="user-copy"><strong>{"Wojtek Kowalski"}</strong><small>{"Administrator"}</small></span>
                <button class="icon-button" aria-label="Open account menu"><Icon name={IconName::ChevronDown} size={16} /></button>
            </div>
        </aside>
    }
}

#[function_component(Topbar)]
fn topbar() -> Html {
    html! {
        <header class="topbar">
            <a class="skip-link" href="#main-content">{"Skip to content"}</a>
            <div class="breadcrumb"><span>{"Finance workspace"}</span><span aria-hidden="true">{"/"}</span><strong>{"2025 close"}</strong></div>
            <label class="search-box">
                <span class="sr-only">{"Search dashboard"}</span>
                <Icon name={IconName::Search} size={17} />
                <input type="search" placeholder="Search transactions, people…" />
            </label>
            <div class="topbar-actions">
                <span class="sync-status"><Icon name={IconName::Check} size={14} />{"Synced 2m ago"}</span>
                <button class="icon-button notification-button" aria-label="Notifications, 1 unread">
                    <Icon name={IconName::Bell} size={18} />
                    <span class="notification-dot" aria-hidden="true" />
                </button>
            </div>
        </header>
    }
}

#[derive(Properties, PartialEq)]
struct OverviewProps {
    period: Period,
    set_period: Callback<Period>,
    first_alert_resolved: bool,
    resolve_alert: Callback<MouseEvent>,
    open_staffing: Callback<MouseEvent>,
    notify_action: Callback<&'static str>,
}

#[function_component(Overview)]
fn overview(props: &OverviewProps) -> Html {
    let data = snapshot(props.period);
    let year_data = snapshot(Period::Year);
    let period_label = if props.period == Period::Year {
        "Full year"
    } else {
        "Q4 2025"
    };
    let chart_style = donut_style(&data.expenses);

    html! {
        <div class="page-shell">
            <section class="page-heading">
                <div>
                    <p class="eyebrow"><span class="live-pulse" aria-hidden="true" />{"Friday, 20 November · Agent online"}</p>
                    <h1>{"Good morning, Wojtek"}</h1>
                    <p>{format!("Your finance loop reconciled {} transactions and found 3 items that need attention.", format_count(year_data.transactions))}</p>
                </div>
                <button
                    class="aesir-button"
                    data-variant="accent"
                    data-size="default"
                    onclick={{
                        let notify = props.notify_action.clone();
                        Callback::from(move |_| notify.emit("CFO workspace is ready for the next agent integration."))
                    }}
                >
                    <Icon name={IconName::Spark} size={16} />
                    {"Ask your CFO"}
                </button>
            </section>

            <section class="metric-grid" aria-label="Financial summary">
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
                    trend="Live bank feed"
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

            <div class="dashboard-grid">
                <section class="aesir-card priority-card" aria-labelledby="priority-title">
                    <div class="aesir-card__header card-title-row">
                        <div>
                            <h2 id="priority-title" class="aesir-card__title">{"Priority inbox"}</h2>
                            <p class="aesir-card__description">{"Evidence gathered by the autonomous finance loop"}</p>
                        </div>
                        <span class="aesir-badge" data-tone="warning">{if props.first_alert_resolved { "2 open" } else { "3 open" }}</span>
                    </div>
                    <div class="aesir-card__content alert-list">
                        if !props.first_alert_resolved {
                            <article class="alert-row alert-critical">
                                <span class="alert-icon"><Icon name={IconName::Document} size={17} /></span>
                                <div class="alert-copy">
                                    <div class="alert-meta"><span class="aesir-badge" data-tone="critical">{"Missing invoice"}</span><span>{"31 Dec · 09:42"}</span></div>
                                    <h3>{"€320.00 payment has no matching document"}</h3>
                                    <p>{"Demo Supply Co. · Girocard purchase · checked files and Slack history"}</p>
                                </div>
                                <button class="aesir-button" data-variant="outline" data-size="sm" onclick={props.resolve_alert.clone()}>{"Mark reviewed"}</button>
                            </article>
                        } else {
                            <div class="resolved-banner" role="status"><Icon name={IconName::Check} size={16} /><span><strong>{"Marked as reviewed."}</strong>{" Audit trail updated and owner notified."}</span></div>
                        }
                        <article class="alert-row">
                            <span class="alert-icon tone-orange"><Icon name={IconName::Activity} size={17} /></span>
                            <div class="alert-copy">
                                <div class="alert-meta"><span class="aesir-badge" data-tone="warning">{"Liquidity"}</span><span>{"Forecast"}</span></div>
                                <h3>{"June runway dropped below safety threshold"}</h3>
                                <p>{"A fictional three-week closure caused a temporary −€12,000.00 overdraft. Buffer rule suggested."}</p>
                            </div>
                            <button
                                class="aesir-button"
                                data-variant="ghost"
                                data-size="sm"
                                onclick={{
                                    let notify = props.notify_action.clone();
                                    Callback::from(move |_| notify.emit("Liquidity insight selected. The full analysis panel is queued for integration."))
                                }}
                            >{"View insight"}</button>
                        </article>
                        <article class="alert-row">
                            <span class="alert-icon tone-blue"><Icon name={IconName::Slack} size={17} /></span>
                            <div class="alert-copy">
                                <div class="alert-meta"><span class="aesir-badge" data-tone="info">{"Awaiting reply"}</span><span>{"Slack · 14m"}</span></div>
                                <h3>{"Expense owner asked to confirm supplier charge"}</h3>
                                <p>{"DM sent to Taylor with transaction context and a secure document upload link."}</p>
                            </div>
                            <button
                                class="aesir-button"
                                data-variant="ghost"
                                data-size="sm"
                                onclick={{
                                    let notify = props.notify_action.clone();
                                    Callback::from(move |_| notify.emit("Slack thread selected. Live Slack deep-linking is the next integration step."))
                                }}
                            >{"Open thread"}</button>
                        </article>
                    </div>
                </section>

                <section class="aesir-card expense-card" aria-labelledby="expense-title">
                    <div class="aesir-card__header card-title-row">
                        <div>
                            <h2 id="expense-title" class="aesir-card__title">{"Expense mix"}</h2>
                            <p class="aesir-card__description">{format!("{} · {} transactions", period_label, data.transactions)}</p>
                        </div>
                        <div class="segmented-control" aria-label="Chart period">
                            <button
                                class={classes!((props.period == Period::Year).then_some("is-active"))}
                                aria-pressed={(props.period == Period::Year).to_string()}
                                onclick={{ let callback = props.set_period.clone(); Callback::from(move |_| callback.emit(Period::Year)) }}
                            >{"Year"}</button>
                            <button
                                class={classes!((props.period == Period::QuarterFour).then_some("is-active"))}
                                aria-pressed={(props.period == Period::QuarterFour).to_string()}
                                onclick={{ let callback = props.set_period.clone(); Callback::from(move |_| callback.emit(Period::QuarterFour)) }}
                            >{"Q4"}</button>
                        </div>
                    </div>
                    <div class="aesir-card__content expense-content">
                        <div class="donut-wrap">
                            <div class="donut" style={chart_style} role="img" aria-label={chart_accessible_label(&data.expenses)}>
                                <div class="donut-center">
                                    <span>{"Total out"}</span>
                                    <strong>{format_compact(data.total_expenses)}</strong>
                                </div>
                            </div>
                        </div>
                        <div class="chart-legend">
                            {for data.expenses.iter().map(|category| html! {
                                <div class="legend-row">
                                    <span class="legend-swatch" style={format!("background:{}", category.color)} />
                                    <span class="legend-label">{category.name}</span>
                                    <strong>{format!("{:.0}%", category.percentage)}</strong>
                                    <small>{format_compact(category.amount)}</small>
                                </div>
                            })}
                        </div>
                    </div>
                    <div class="aesir-card__footer chart-footer">
                        <Icon name={IconName::Check} size={14} />
                        <span>{"Calculated from the synthetic 2025 demo statement"}</span>
                    </div>
                </section>
            </div>

            <section class="lower-grid">
                <div class="aesir-card connection-card">
                    <div class="aesir-card__header card-title-row">
                        <div><h2 class="aesir-card__title">{"Finance loop"}</h2><p class="aesir-card__description">{"Continuous evidence chain"}</p></div>
                        <span class="aesir-badge" data-tone="success"><span class="live-pulse" aria-hidden="true" />{"Healthy"}</span>
                    </div>
                    <div class="aesir-card__content connection-flow">
                        <ConnectionStep icon={IconName::Bank} title="Bank feed" detail="600 events" status="Synced" />
                        <span class="flow-line" aria-hidden="true" />
                        <ConnectionStep icon={IconName::Document} title="Invoice match" detail="96.8% matched" status="Checking" />
                        <span class="flow-line" aria-hidden="true" />
                        <ConnectionStep icon={IconName::Slack} title="Team context" detail="4 owners" status="Connected" />
                        <span class="flow-line" aria-hidden="true" />
                        <ConnectionStep icon={IconName::Lock} title="IAM guard" detail="Least privilege" status="Enforced" />
                    </div>
                </div>
                <div class="aesir-card payroll-card">
                    <div class="aesir-card__header card-title-row">
                        <div><h2 class="aesir-card__title">{"Next payroll"}</h2><p class="aesir-card__description">{"Scheduled for 28 November"}</p></div>
                        <Icon name={IconName::Clock} size={18} />
                    </div>
                    <div class="aesir-card__content payroll-preview">
                        <div><span>{"4 people"}</span><strong>{format_currency(monthly_payroll())}</strong></div>
                        <div class="avatar-stack" aria-label="Payroll recipients">
                            {for EMPLOYEES.iter().map(|employee| html! {<span class={classes!("aesir-avatar", "avatar-sm", employee.gradient)} role="img" aria-label={employee.name}>{employee.initials}</span>})}
                        </div>
                        <button class="aesir-button" data-variant="secondary" data-size="sm" onclick={props.open_staffing.clone()}>{"Review staffing"}</button>
                    </div>
                </div>
            </section>
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
        <article class="metric-card">
            <div class={classes!("metric-icon", format!("tone-{}", props.tone))}><Icon name={props.icon} size={18} /></div>
            <p>{props.label}</p>
            <strong class="metric-value">{props.value.clone()}</strong>
            <span class="metric-note">{props.note}</span>
            <span class={classes!("metric-trend", format!("trend-{}", props.tone))}>{props.trend}</span>
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
        <div class="connection-step">
            <span class="connection-icon"><Icon name={props.icon} size={17} /></span>
            <div><strong>{props.title}</strong><small>{props.detail}</small></div>
            <span class="connection-status"><Icon name={IconName::Check} size={12} />{props.status}</span>
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
        <div class="page-shell">
            <section class="page-heading staffing-heading">
                <div>
                    <p class="eyebrow">{"People operations"}</p>
                    <h1>{"Staffing & payroll"}</h1>
                    <p>{"A current view of people, responsibilities and recurring salary commitments."}</p>
                </div>
                <button class="aesir-button" data-variant="outline" data-size="default" onclick={props.back_to_overview.clone()}>{"Back to overview"}</button>
            </section>

            <section class="staffing-summary" aria-label="Staffing summary">
                <div><span>{"Active team"}</span><strong>{"4"}</strong><small>{"All payroll profiles verified"}</small></div>
                <div><span>{"Monthly net payroll"}</span><strong>{format_currency(monthly_payroll())}</strong><small>{"Paid on the 28th"}</small></div>
                <div><span>{"Employer contributions"}</span><strong>{"€2,800.00"}</strong><small>{"Social security · monthly"}</small></div>
                <div><span>{"Next payroll"}</span><strong>{"8 days"}</strong><small>{"28 November 2026"}</small></div>
            </section>

            <section class="aesir-card staff-card" aria-labelledby="team-title">
                <div class="aesir-card__header card-title-row staff-card-heading">
                    <div><h2 id="team-title" class="aesir-card__title">{"Team directory"}</h2><p class="aesir-card__description">{"Salary data from the synthetic Demo Bistro scenario"}</p></div>
                    <div class="staff-actions">
                        <label class="small-search"><Icon name={IconName::Search} size={15} /><span class="sr-only">{"Search team"}</span><input type="search" placeholder="Search people" /></label>
                        <button
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
                <div class="staff-table" role="table" aria-label="Employee salaries">
                    <div class="staff-table-header" role="row">
                        <span role="columnheader">{"Employee"}</span><span role="columnheader">{"Team"}</span><span role="columnheader">{"Monthly net"}</span><span role="columnheader">{"Status"}</span>
                    </div>
                    {for EMPLOYEES.iter().map(|employee| html! {
                        <div class="staff-row" role="row">
                            <div class="employee-cell" role="cell">
                                <span class={classes!("aesir-avatar", "avatar-lg", employee.gradient)} role="img" aria-label={employee.name}>{employee.initials}</span>
                                <span><strong>{employee.name}</strong><small>{employee.role}</small></span>
                            </div>
                            <span class="team-cell" role="cell"><span class="team-icon"><Icon name={if employee.team == "Leadership" { IconName::Spark } else { IconName::Team }} size={14} /></span>{employee.team}</span>
                            <span class="salary-cell" role="cell"><strong>{format_currency(employee.salary)}</strong><small>{"€ · monthly"}</small></span>
                            <span role="cell"><span class="aesir-badge" data-tone="success"><span class="status-dot" />{"Active"}</span></span>
                        </div>
                    })}
                </div>
                <div class="aesir-card__footer staff-footer">
                    <span><Icon name={IconName::Lock} size={14} />{"Salary access follows IAM role policy"}</span>
                    <span>{"Last verified 2 minutes ago"}</span>
                </div>
            </section>

            <section class="staff-insight">
                <div class="insight-icon"><Icon name={IconName::Spark} size={20} /></div>
                <div><strong>{"CFO insight"}</strong><p>{"Payroll represents a stable €9,200 monthly commitment. The June closure kept all salaries running, which explains 76.7% of the temporary liquidity gap."}</p></div>
                <button
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

fn donut_style(categories: &[ExpenseCategory]) -> String {
    let mut cursor = 0.0;
    let mut stops = Vec::new();
    for category in categories {
        let end = cursor + category.percentage;
        stops.push(format!("{} {:.2}% {:.2}%", category.color, cursor, end));
        cursor = end;
    }
    format!("background: conic-gradient({})", stops.join(", "))
}

fn chart_accessible_label(categories: &[ExpenseCategory]) -> String {
    let details = categories
        .iter()
        .map(|category| format!("{} {:.0}%", category.name, category.percentage))
        .collect::<Vec<_>>()
        .join(", ");
    format!("Expense distribution: {details}")
}

fn main() {
    yew::Renderer::<App>::new().render();
}
