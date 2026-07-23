import math
import re
import unittest
from pathlib import Path

from standardize_gui import (
    LoadedDataset,
    PLOT_EXPORT_SCALE,
    QUICK_SELECT_COLUMNS,
    VALUE_LABEL_FONT_SIZE,
    PlotSeries,
    StandardizeApp,
    StandardizeStats,
    calculate_original_stats,
    calculate_box_plot,
    calculate_standardize_against,
    calculate_standardize,
    cropped_single_line_text,
    percentile,
    plot_font_size,
    quick_select_column_indices,
)


class StandardizeCalculationTests(unittest.TestCase):
    def test_matches_excel_standardize_with_sample_stdev(self):
        values = [10, 20, 30]
        standardized, stats = calculate_standardize(values)

        self.assertEqual(stats.count, 3)
        self.assertAlmostEqual(stats.mean, 20.0)
        self.assertAlmostEqual(stats.sample_stdev, 10.0)
        self.assertAlmostEqual(standardized[0], -1.0)
        self.assertAlmostEqual(standardized[1], 0.0)
        self.assertAlmostEqual(standardized[2], 1.0)

    def test_ignores_non_numeric_values_like_excel_aggregate_functions(self):
        standardized, stats = calculate_standardize([1, "", None, 3, "5"])

        self.assertEqual(stats.count, 3)
        self.assertAlmostEqual(stats.mean, 3.0)
        self.assertEqual(standardized[1], None)
        self.assertEqual(standardized[2], None)
        self.assertTrue(math.isclose(standardized[4], 1.0))

    def test_rejects_constant_values(self):
        with self.assertRaises(ValueError):
            calculate_standardize([7, 7, 7])

    def test_percentile_uses_linear_interpolation(self):
        values = [1, 2, 3, 4]

        self.assertAlmostEqual(percentile(values, 0.25), 1.75)
        self.assertAlmostEqual(percentile(values, 0.5), 2.5)
        self.assertAlmostEqual(percentile(values, 0.75), 3.25)

    def test_box_plot_summary_marks_iqr_outliers(self):
        summary = calculate_box_plot([1, 2, 3, 4, 100])

        self.assertAlmostEqual(summary.q1, 2.0)
        self.assertAlmostEqual(summary.median, 3.0)
        self.assertAlmostEqual(summary.q3, 4.0)
        self.assertAlmostEqual(summary.lower_whisker, 1.0)
        self.assertAlmostEqual(summary.upper_whisker, 4.0)
        self.assertEqual(summary.outliers, [100])

    def test_svg_export_contains_internal_original_value_labels(self):
        app = object.__new__(StandardizeApp)
        app.plot_series = [
            PlotSeries(
                name="Signal",
                values=[-2.0, -1.0, -1.0, 4.0],
                stats=StandardizeStats(
                    count=4,
                    mean=20.0,
                    sample_stdev=10.0,
                    minimum=10.0,
                    maximum=30.0,
                ),
                color="#1f77b4",
            )
        ]

        layout = app._build_plot_layout(900, 600)
        self.assertIsNotNone(layout)
        svg = app._plot_to_svg(layout)

        self.assertIn("STANDARDIZE Box Plot", svg)
        self.assertIn("max 30", svg)
        self.assertIn("min 10", svg)
        self.assertIn("avg 20", svg)
        self.assertRegex(svg, rf'font-size="{VALUE_LABEL_FONT_SIZE}"[^>]*>max 30</text>')
        self.assertRegex(svg, rf'font-size="{VALUE_LABEL_FONT_SIZE}"[^>]*>min 10</text>')
        self.assertRegex(svg, rf'font-size="{plot_font_size(VALUE_LABEL_FONT_SIZE)}"[^>]*>avg 20</text>')
        self.assertRegex(svg, r'text-anchor="middle"[^>]*>avg 20</text>')
        self.assertNotIn('stroke="#dddddd"', svg)
        self.assertNotIn('stroke="#111111"', svg)
        self.assertIn('stroke="#9a9a9a"', svg)
        self.assertIn('stroke-dasharray="3 3"', svg)
        self.assertNotIn("Row index", svg)

        summary = layout.groups[0].series[0][1]
        true_min_y = app._plot_y(layout, summary.minimum)
        min_label_y = min(layout.plot_bottom - 12, true_min_y + 5)
        expected_avg_label_y = app._avg_label_y_below_min(layout, min_label_y, VALUE_LABEL_FONT_SIZE)
        min_label_match = re.search(r'<text x="[^"]+" y="([^"]+)"[^>]*>min 10</text>', svg)
        avg_label_match = re.search(r'<text x="[^"]+" y="([^"]+)"[^>]*>avg 20</text>', svg)
        self.assertIsNotNone(min_label_match)
        self.assertIsNotNone(avg_label_match)
        self.assertGreater(float(avg_label_match.group(1)), float(min_label_match.group(1)))
        self.assertGreater(float(avg_label_match.group(1)) - float(min_label_match.group(1)), 20)
        self.assertAlmostEqual(float(avg_label_match.group(1)), expected_avg_label_y, places=2)

    def test_cropped_single_line_text_limits_file_labels(self):
        label = cropped_single_line_text("very_long_file_name_that_should_wrap.csv\nsecond line", 60, 9)

        self.assertNotIn("\n", label)
        self.assertLessEqual(len(label), 6)
        self.assertTrue(label.endswith("..."))

    def test_quick_select_column_indices_matches_score_metrics(self):
        columns = [
            "Name",
            "dG_separated",
            "dG_separated/dSASAx100",
            "ranking_score",
            "interface_score",
            "Other",
        ]

        self.assertEqual(len(QUICK_SELECT_COLUMNS), 9)
        self.assertNotIn("dG_separated", QUICK_SELECT_COLUMNS)
        self.assertEqual(quick_select_column_indices(columns), [2, 3, 4])

    def test_reference_stats_are_used_for_other_files(self):
        reference_stats = calculate_original_stats([10, 20, 30], require_sample_stdev=True)
        standardized, stats = calculate_standardize_against([20, 30, 40], reference_stats)

        self.assertAlmostEqual(stats.mean, 30.0)
        self.assertAlmostEqual(standardized[0], 0.0)
        self.assertAlmostEqual(standardized[1], 1.0)
        self.assertAlmostEqual(standardized[2], 2.0)

    def test_plot_layout_groups_series_by_column(self):
        app = object.__new__(StandardizeApp)
        app.plot_series = [
            PlotSeries("file_a.csv", [-1.0, 0.0, 1.0], StandardizeStats(3, 2.0, 1.0, 1.0, 3.0), "#1f77b4", "file_a.csv", "A"),
            PlotSeries("file_b.csv", [0.0, 1.0, 2.0], StandardizeStats(3, 3.0, 1.0, 2.0, 4.0), "#d62728", "file_b.csv", "A"),
            PlotSeries("file_a.csv", [-2.0, 0.0, 2.0], StandardizeStats(3, 5.0, 2.0, 3.0, 7.0), "#1f77b4", "file_a.csv", "B"),
        ]

        layout = app._build_plot_layout(900, 600)

        self.assertIsNotNone(layout)
        self.assertEqual([group.column_name for group in layout.groups], ["A", "B"])
        self.assertEqual(len(layout.groups[0].series), 2)
        self.assertEqual(len(layout.groups[1].series), 1)

    def test_png_export_writes_png_file(self):
        app = object.__new__(StandardizeApp)
        app.plot_series = [
            PlotSeries(
                name="file_a.csv",
                values=[-1.0, 0.0, 1.0],
                stats=StandardizeStats(3, 20.0, 10.0, 10.0, 30.0),
                color="#1f77b4",
                file_name="file_a.csv",
                column_name="Signal",
            )
        ]
        layout = app._build_plot_layout(900, 600)
        self.assertIsNotNone(layout)

        output_path = Path.cwd() / "plot_export_test.png"
        try:
            app._plot_to_png(layout, output_path)
            self.assertTrue(output_path.exists())
            self.assertGreater(output_path.stat().st_size, 100)
            from PIL import Image

            with Image.open(output_path) as image:
                self.assertEqual(image.size, (layout.content_width * PLOT_EXPORT_SCALE, layout.height * PLOT_EXPORT_SCALE))
        finally:
            if output_path.exists():
                output_path.unlink()

    def test_filter_new_paths_skips_loaded_and_duplicate_paths(self):
        app = object.__new__(StandardizeApp)
        existing_path = Path.cwd() / "existing.csv"
        new_path = Path.cwd() / "new.csv"
        app.datasets = [
            LoadedDataset(existing_path, "existing.csv", "", object())
        ]

        filtered = app._filter_new_paths([existing_path, new_path, new_path])

        self.assertEqual(filtered, [new_path])

    def test_unique_display_name_adds_suffix_for_duplicates(self):
        app = object.__new__(StandardizeApp)

        name = app._unique_display_name("data.csv", {"data.csv", "data (2).csv"})

        self.assertEqual(name, "data (3).csv")


if __name__ == "__main__":
    unittest.main()
