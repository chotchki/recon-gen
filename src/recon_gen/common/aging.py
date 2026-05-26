"""Shared aging-bucket visual builder.

Used by both Payment Recon and Account Recon Exceptions tabs to render
horizontal bar charts showing exception counts by age band.
"""

from __future__ import annotations

from recon_gen.common.models import (
    AxisLabelOptions,
    BarChartAggregatedFieldWells,
    BarChartConfiguration,
    BarChartFieldWells,
    BarChartVisual,
    CategoricalDimensionField,
    CategoricalMeasureField,
    ChartAxisLabelOptions,
    ColumnIdentifier,
    DimensionField,
    MeasureField,
    Visual,
    VisualSubtitleLabelOptions,
    VisualTitleLabelOptions,
)


def aging_bar_visual(
    visual_id: str,  # typing-smell: ignore[bare-str-id]: callers pass raw analyst-facing visual IDs
    title: str,
    subtitle: str,
    dataset_id: str,
    count_column: str,
) -> Visual:
    """Horizontal bar chart showing exception count by aging bucket."""
    return Visual(
        BarChartVisual=BarChartVisual(
            VisualId=visual_id,
            Title=VisualTitleLabelOptions(
                Visibility="VISIBLE",
                FormatText={"PlainText": title},
            ),
            Subtitle=VisualSubtitleLabelOptions(
                Visibility="VISIBLE",
                FormatText={"PlainText": subtitle},
            ),
            ChartConfiguration=BarChartConfiguration(
                FieldWells=BarChartFieldWells(
                    BarChartAggregatedFieldWells=BarChartAggregatedFieldWells(
                        Category=[DimensionField(
                            CategoricalDimensionField=CategoricalDimensionField(
                                FieldId=f"{visual_id}-dim",
                                Column=ColumnIdentifier(
                                    DataSetIdentifier=dataset_id,
                                    ColumnName="aging_bucket",
                                ),
                            )
                        )],
                        Values=[MeasureField(
                            CategoricalMeasureField=CategoricalMeasureField(
                                FieldId=f"{visual_id}-count",
                                Column=ColumnIdentifier(
                                    DataSetIdentifier=dataset_id,
                                    ColumnName=count_column,
                                ),
                                AggregationFunction="COUNT",
                            )
                        )],
                    )
                ),
                Orientation="HORIZONTAL",
                BarsArrangement="CLUSTERED",
                CategoryLabelOptions=ChartAxisLabelOptions(
                    AxisLabelOptions=[AxisLabelOptions(CustomLabel="Age")],
                ),
                ValueLabelOptions=ChartAxisLabelOptions(
                    AxisLabelOptions=[AxisLabelOptions(CustomLabel="Count")],
                ),
            ),
        )
    )
