//! A deterministic mock cash scenario. Coordinates are [balance in EUR, elapsed days].

#[cfg(any(target_arch = "wasm32", test))]
const START_BALANCE: f64 = 42_000.00;
#[cfg(any(target_arch = "wasm32", test))]
const END_DAY: i32 = 92; // 20 November 2026 through 20 February 2027, inclusive.

#[cfg(any(target_arch = "wasm32", test))]
fn scenario_points() -> Vec<[f64; 2]> {
    let mut balance = START_BALANCE;
    (0..=END_DAY)
        .map(|day| {
            if day > 0 {
                // Illustrative recurring revenue, procurement, rent and payroll.
                // These values are deliberately not fitted to historical transactions.
                balance += if day % 7 < 5 { 745.0 } else { 1_160.0 };
                balance -= 410.0 + f64::from(day % 5) * 38.0;
                if [8, 38, 69].contains(&day) {
                    balance -= 9_200.0;
                }
                if [11, 42, 73].contains(&day) {
                    balance -= 2_800.0;
                }
            }
            [balance, f64::from(day)]
        })
        .collect()
}

#[cfg(any(target_arch = "wasm32", test))]
fn date_label(day: f64) -> String {
    if !day.is_finite() || !(-19.0..=130.0).contains(&day) {
        return String::new();
    }
    let offset = day.round() as i32;
    if offset < 11 {
        format!("{} Nov 2026", 20 + offset)
    } else if offset < 42 {
        format!("{} Dec 2026", offset - 10)
    } else if offset < 73 {
        format!("{} Jan 2027", offset - 41)
    } else if offset < 101 {
        format!("{} Feb 2027", offset - 72)
    } else {
        format!("{} Mar 2027", offset - 100)
    }
}

#[cfg(any(target_arch = "wasm32", test))]
#[path = "../../src/data.rs"]
#[allow(dead_code)]
mod data;

#[cfg(target_arch = "wasm32")]
mod web {
    use super::data::{self, ExpenseCategory, Period};
    use super::{END_DAY, START_BALANCE, date_label, scenario_points};
    use eframe::egui::{self, Color32, Key, Modifiers};
    use egui_plot::{Legend, Line, LineStyle, Plot, PlotBounds, Points};
    use std::cell::RefCell;
    use wasm_bindgen::prelude::*;

    thread_local! {
        static RUNNER: RefCell<Option<eframe::WebRunner>> = const { RefCell::new(None) };
        static KEY_HANDLER: RefCell<Option<(web_sys::HtmlCanvasElement, Closure<dyn FnMut(web_sys::KeyboardEvent)>)>> = const { RefCell::new(None) };
    }

    struct ForecastApp {
        points: Vec<[f64; 2]>,
        canvas: web_sys::HtmlCanvasElement,
        pending_zoom: f64,
        reset: bool,
        show_expenses: bool,
        expense_period: Period,
    }

    impl ForecastApp {
        fn new(cc: &eframe::CreationContext<'_>, canvas: web_sys::HtmlCanvasElement) -> Self {
            // Ctrl +/- changes data bounds only, never egui's UI scale as well.
            cc.egui_ctx
                .options_mut(|options| options.zoom_with_keyboard = false);
            let mut visuals = egui::Visuals::dark();
            visuals.panel_fill = Color32::from_rgb(24, 24, 24);
            visuals.extreme_bg_color = Color32::from_rgb(16, 16, 16);
            visuals.selection.bg_fill = Color32::from_rgb(37, 102, 78);
            cc.egui_ctx.set_visuals(visuals);
            Self {
                points: scenario_points(),
                canvas,
                pending_zoom: 1.0,
                reset: true,
                show_expenses: false,
                expense_period: Period::Year,
            }
        }

        fn canvas_focused(&self) -> bool {
            web_sys::window()
                .and_then(|window| window.document())
                .and_then(|document| document.active_element())
                .is_some_and(|element| {
                    element == self.canvas.clone().unchecked_into::<web_sys::Element>()
                })
        }

        fn initial_bounds() -> PlotBounds {
            PlotBounds::from_min_max([23_000.0, -4.0], [39_000.0, f64::from(END_DAY) + 4.0])
        }
    }

    impl eframe::App for ForecastApp {
        fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
            if self.canvas_focused() {
                ctx.input_mut(|input| {
                    if input.consume_key(Modifiers::NONE, Key::Num1) {
                        self.show_expenses = false;
                    }
                    if input.consume_key(Modifiers::NONE, Key::Num2) {
                        self.show_expenses = true;
                    }
                    if input.consume_key(Modifiers::NONE, Key::Y) {
                        self.expense_period = Period::Year;
                    }
                    if input.consume_key(Modifiers::NONE, Key::Q) {
                        self.expense_period = Period::QuarterFour;
                    }
                });
            }
            set_attribute(
                &self.canvas,
                "data-chart-view",
                if self.show_expenses {
                    "expenses"
                } else {
                    "forecast"
                },
            );
            if !self.show_expenses && self.canvas_focused() {
                ctx.input_mut(|input| {
                    let plus = input.consume_key(Modifiers::CTRL, Key::Plus)
                        | input.consume_key(Modifiers::CTRL, Key::Equals);
                    let minus = input.consume_key(Modifiers::CTRL, Key::Minus);
                    if plus {
                        self.pending_zoom *= 0.8;
                    }
                    if minus {
                        self.pending_zoom *= 1.25;
                    }
                    if input.consume_key(Modifiers::CTRL, Key::Num0) {
                        self.reset = true;
                    }
                });
            }
            egui::CentralPanel::default().show(ctx, |ui| {
                ui.horizontal_wrapped(|ui| {
                    ui.selectable_value(&mut self.show_expenses, false, "Cash balance forecast");
                    ui.selectable_value(&mut self.show_expenses, true, "Expense mix");
                    ui.weak("Keys 1 / 2 · Tab leaves chart");
                });
                ui.separator();
                if self.show_expenses {
                    self.expense_chart(ui);
                    return;
                }
                ui.horizontal_wrapped(|ui| {
                    if ui.button("Zoom in").clicked() {
                        self.pending_zoom *= 0.8;
                    }
                    if ui.button("Zoom out").clicked() {
                        self.pending_zoom *= 1.25;
                    }
                    if ui.button("Reset").clicked() {
                        self.reset = true;
                    }
                    ui.label("Focus chart: Ctrl + / Ctrl − / Ctrl 0");
                });
                ui.label("MOCK SCENARIO · 20 Nov 2026 – 20 Feb 2027");
                let zoom = std::mem::replace(&mut self.pending_zoom, 1.0);
                let reset = std::mem::replace(&mut self.reset, false);
                let plot_response = Plot::new("lantern-cash-balance-scenario")
                    .height((ui.available_height() - 23.0).max(120.0))
                    .x_axis_label("Cash balance (EUR)")
                    .y_axis_label("Date")
                    .x_axis_formatter(|mark, _| format!("€{:.0}", mark.value))
                    .y_axis_formatter(|mark, _| date_label(mark.value))
                    .label_formatter(|name, point| {
                        format!("{name}\n{}\n€{:.2}", date_label(point.y), point.x)
                    })
                    .legend(Legend::default())
                    .show(ui, |plot_ui| {
                        if reset {
                            plot_ui.set_plot_bounds(Self::initial_bounds());
                        } else if zoom != 1.0 {
                            let bounds = plot_ui.plot_bounds();
                            let center = bounds.center();
                            let half_width = (bounds.width() * zoom * 0.5).clamp(100.0, 100_000.0);
                            let half_height = (bounds.height() * zoom * 0.5).clamp(2.0, 150.0);
                            plot_ui.set_plot_bounds(PlotBounds::from_min_max(
                                [center.x - half_width, center.y - half_height],
                                [center.x + half_width, center.y + half_height],
                            ));
                        }
                        plot_ui.line(
                            Line::new("Illustrative future", self.points.clone())
                                .color(Color32::from_rgb(218, 158, 91))
                                .style(LineStyle::dashed_dense())
                                .width(2.0),
                        );
                        plot_ui.points(
                            Points::new(
                                "Synthetic demo starting balance",
                                vec![[START_BALANCE, 0.0]],
                            )
                            .color(Color32::from_rgb(114, 181, 143))
                            .radius(5.0),
                        );
                    });
                set_attribute(
                    &self.canvas,
                    "data-plot-x-min",
                    &plot_response.transform.bounds().min()[0].to_string(),
                );
                set_attribute(
                    &self.canvas,
                    "data-plot-x-max",
                    &plot_response.transform.bounds().max()[0].to_string(),
                );
                ui.label("€42,000.00 seed = synthetic demo closing balance; future path is synthetic.");
            });
        }
    }

    impl ForecastApp {
        fn expense_chart(&mut self, ui: &mut egui::Ui) {
            ui.horizontal(|ui| {
                ui.selectable_value(&mut self.expense_period, Period::Year, "Year 2025");
                ui.selectable_value(&mut self.expense_period, Period::QuarterFour, "Q4 2025");
                ui.weak("Keys Y / Q");
            });
            let snapshot = data::snapshot(self.expense_period);
            set_attribute(
                &self.canvas,
                "data-chart-period",
                if self.expense_period == Period::Year {
                    "year"
                } else {
                    "q4"
                },
            );
            set_attribute(
                &self.canvas,
                "data-expense-total",
                &format!("{:.2}", snapshot.total_expenses),
            );
            ui.label(format!(
                "Recorded expenses · {} transactions",
                snapshot.transactions
            ));
            ui.label(format!("Total out: €{:.2}", snapshot.total_expenses));
            ui.add_space(10.0);
            egui::ScrollArea::vertical().show(ui, |ui| {
                if ui.available_width() >= 540.0 {
                    ui.horizontal(|ui| {
                        draw_pie(ui, &snapshot.expenses, 230.0);
                        ui.add_space(18.0);
                        expense_legend(ui, &snapshot.expenses);
                    });
                } else {
                    draw_pie(ui, &snapshot.expenses, 150.0_f32.min(ui.available_width()));
                    ui.add_space(8.0);
                    expense_legend(ui, &snapshot.expenses);
                }
                ui.add_space(10.0);
                ui.label("Calculated from the synthetic 2025 demo statement. No forecast data in this chart.");
            });
        }
    }

    fn set_attribute(canvas: &web_sys::HtmlCanvasElement, key: &str, value: &str) {
        if canvas.get_attribute(key).as_deref() != Some(value) {
            let _ = canvas.set_attribute(key, value);
        }
    }

    fn expense_color(category: &ExpenseCategory) -> Color32 {
        let hex = category.color.trim_start_matches('#');
        u32::from_str_radix(hex, 16)
            .map(|rgb| Color32::from_rgb((rgb >> 16) as u8, (rgb >> 8) as u8, rgb as u8))
            .unwrap_or(Color32::GRAY)
    }

    fn expense_legend(ui: &mut egui::Ui, categories: &[ExpenseCategory]) {
        egui::Grid::new("lantern-expense-legend")
            .striped(true)
            .spacing([12.0, 12.0])
            .show(ui, |ui| {
                for category in categories {
                    ui.colored_label(expense_color(category), category.name);
                    ui.label(format!("{:.0}%", category.percentage));
                    ui.label(format!("€{:.2}", category.amount));
                    ui.end_row();
                }
            });
    }

    fn draw_pie(ui: &mut egui::Ui, categories: &[ExpenseCategory], diameter: f32) {
        let (rect, response) =
            ui.allocate_exact_size(egui::vec2(diameter, diameter), egui::Sense::hover());
        let center = rect.center();
        let radius = diameter * 0.46;
        let mut start = -std::f32::consts::FRAC_PI_2;
        let pointer = response.hover_pos();
        let mut hovered = None;
        for category in categories {
            let sweep = (category.percentage * std::f64::consts::TAU / 100.0) as f32;
            let end = start + sweep;
            // Keep each polygon convex (at most a half circle), without seams
            // from separately antialiasing every radial triangle.
            let pieces = ((sweep / std::f32::consts::PI).ceil() as usize).max(1);
            for piece in 0..pieces {
                let a = start + sweep * piece as f32 / pieces as f32;
                let b = start + sweep * (piece + 1) as f32 / pieces as f32;
                let segments = (((b - a) * 28.0).ceil() as usize).max(1);
                let mut vertices = vec![center];
                vertices.extend((0..=segments).map(|segment| {
                    let angle = a + (b - a) * segment as f32 / segments as f32;
                    center + egui::vec2(angle.cos(), angle.sin()) * radius
                }));
                ui.painter().add(egui::Shape::convex_polygon(
                    vertices,
                    expense_color(category),
                    egui::Stroke::NONE,
                ));
            }
            if let Some(pointer) = pointer {
                let delta = pointer - center;
                let angle = (delta.y.atan2(delta.x) + std::f32::consts::FRAC_PI_2)
                    .rem_euclid(std::f32::consts::TAU)
                    - std::f32::consts::FRAC_PI_2;
                if delta.length() <= radius && angle >= start && angle < end {
                    hovered = Some(category);
                }
            }
            start = end;
        }
        if let Some(category) = hovered {
            response.on_hover_text(format!(
                "{} · {:.1}% · €{:.2}",
                category.name, category.percentage, category.amount
            ));
        }
    }

    /// Starts embedded WebGL charts. The caller owns the canvas and its 500px layout slot.
    #[wasm_bindgen]
    pub async fn start(canvas: web_sys::HtmlCanvasElement) -> Result<(), JsValue> {
        stop();
        canvas.set_tab_index(0);
        let runner = eframe::WebRunner::new();
        let app_canvas = canvas.clone();
        // eframe 0.32.3 selects Glow when only its glow renderer feature is enabled.
        runner
            .start(
                canvas.clone(),
                eframe::WebOptions::default(),
                Box::new(move |cc| Ok(Box::new(ForecastApp::new(cc, app_canvas)))),
            )
            .await?;
        // eframe 0.32.3 does not suppress native browser Ctrl +/- zoom itself.
        // This listener is scoped to this canvas and leaves egui to handle data zoom.
        // Tab exits to the surrounding page instead of becoming trapped in the canvas.
        let handler = Closure::<dyn FnMut(web_sys::KeyboardEvent)>::new(
            move |event: web_sys::KeyboardEvent| {
                if event.key() == "Tab" {
                    event.stop_immediate_propagation();
                } else if event.ctrl_key() && ["+", "=", "-", "0"].contains(&event.key().as_str()) {
                    event.prevent_default();
                }
            },
        );
        if let Err(error) = canvas.add_event_listener_with_callback_and_bool(
            "keydown",
            handler.as_ref().unchecked_ref(),
            true,
        ) {
            runner.destroy();
            return Err(error);
        }
        KEY_HANDLER.with(|current| current.replace(Some((canvas, handler))));
        RUNNER.with(|current| current.replace(Some(runner)));
        Ok(())
    }

    /// Releases animation callbacks, browser listeners and the WebGL renderer before SPA unmount.
    #[wasm_bindgen]
    pub fn stop() {
        KEY_HANDLER.with(|current| {
            if let Some((canvas, handler)) = current.borrow_mut().take() {
                let _ = canvas.remove_event_listener_with_callback_and_bool(
                    "keydown",
                    handler.as_ref().unchecked_ref(),
                    true,
                );
            }
        });
        RUNNER.with(|current| {
            if let Some(runner) = current.borrow_mut().take() {
                runner.destroy();
            }
        });
    }
}

#[cfg(target_arch = "wasm32")]
pub use web::{start, stop};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scenario_preserves_seed_and_daily_dates() {
        let points = scenario_points();
        assert_eq!(points.len(), 93);
        assert_eq!(points[0], [START_BALANCE, 0.0]);
        assert_eq!(points.last().unwrap()[1], 92.0);
        assert!(
            points
                .iter()
                .enumerate()
                .all(|(i, point)| point[0].is_finite() && point[1] == i as f64)
        );
    }

    #[test]
    fn dates_cross_month_and_year_boundaries() {
        assert_eq!(date_label(0.0), "20 Nov 2026");
        assert_eq!(date_label(11.0), "1 Dec 2026");
        assert_eq!(date_label(42.0), "1 Jan 2027");
        assert_eq!(date_label(73.0), "1 Feb 2027");
        assert_eq!(date_label(92.0), "20 Feb 2027");
    }

    #[test]
    fn expense_periods_reconcile_to_their_categories() {
        for period in [data::Period::Year, data::Period::QuarterFour] {
            let snapshot = data::snapshot(period);
            assert!(
                (snapshot
                    .expenses
                    .iter()
                    .map(|item| item.amount)
                    .sum::<f64>()
                    - snapshot.total_expenses)
                    .abs()
                    < 0.01
            );
            assert!(
                (snapshot
                    .expenses
                    .iter()
                    .map(|item| item.percentage)
                    .sum::<f64>()
                    - 100.0)
                    .abs()
                    < 0.001
            );
        }
        assert!(
            data::snapshot(data::Period::Year).total_expenses
                > data::snapshot(data::Period::QuarterFour).total_expenses
        );
    }
}
