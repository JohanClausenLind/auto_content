import type { DatasetTable, MapScene as Spec } from "@content-factory/content-schema-ts";
import { geoAlbersUsa, geoEqualEarth, geoMercator, geoNaturalEarth1, geoPath, type GeoProjection } from "d3";
import type { FeatureCollection } from "geojson";
import { useEffect, useMemo, useState, type ReactElement } from "react";
import { continueRender, delayRender, useCurrentFrame } from "remotion";
import { refClassification } from "@content-factory/content-ui";
import { feature } from "topojson-client";
import type { Topology } from "topojson-specification";

import { useSceneEnv } from "../context";
import { enter, motionFrames, progress } from "../motion";
import { PlaceholderCard } from "./Placeholder";
import { Lines, minTextPx, SceneFrame, useFittedText, useSceneGeometry } from "./common";

/** The four projections the MapScene contract names. All are d3-geo: the contract describes a
 * topojson choropleth, not a tiled basemap, which is why this scene needs no WebGL and no tiles. */
const PROJECTIONS: Record<Spec["projection"], () => GeoProjection> = {
  equalEarth: geoEqualEarth,
  mercator: geoMercator,
  albersUsa: geoAlbersUsa,
  naturalEarth1: geoNaturalEarth1,
};

/**
 * Region key → value for a choropleth fill.
 *
 * Join convention: the dataset's FIRST column holds the region key, matched against the topojson
 * feature id. `column` names the value column and defaults to the second column. Rows whose value
 * is not a finite number are skipped rather than coerced, so a missing region stays unfilled
 * instead of rendering as a confident zero.
 */
export function regionValues(dataset: DatasetTable | undefined, column: string | null): Map<string, number> {
  const out = new Map<string, number>();
  if (!dataset) return out;
  const keyColumn = dataset.columns[0];
  const valueColumn = column ?? dataset.columns[1];
  if (keyColumn === undefined || valueColumn === undefined) return out;
  for (const row of dataset.rows) {
    const value = row[valueColumn];
    if (typeof value === "number" && Number.isFinite(value)) out.set(String(row[keyColumn] ?? ""), value);
  }
  return out;
}

/** Fill opacity for one region: 0.15 at the bottom of the range, 1 at the top. A single-value
 * dataset gets the full weight rather than a divide-by-zero. */
export function fillWeight(value: number | undefined, max: number): number {
  if (value === undefined || max <= 0) return 0;
  return 0.15 + 0.85 * Math.min(1, value / max);
}

interface Loaded {
  collection: FeatureCollection;
}

/**
 * Choropleth map: topojson decoded to GeoJSON, projected with d3-geo, drawn as plain SVG paths.
 * Deterministic by construction — no WebGL, no tile requests, and the only clock is the frame.
 * The topology is fetched once per mount from the bundle asset and the render is held until it
 * resolves; a missing or unreadable asset degrades to the labelled placeholder card rather than
 * failing the whole video.
 *
 * Asset requirement — RING WINDING. d3-geo treats polygons as spherical, so a counter-clockwise
 * exterior ring means "the whole globe except this shape": one badly wound region floods the
 * entire plot with its fill. Exterior rings must be clockwise in lon/lat. Topology published by
 * the usual sources (Natural Earth, us-atlas, world-atlas) is already correct; hand-built or
 * re-exported topology is where this bites, and it looks like a projection bug rather than a
 * data bug.
 */
export function MapScene({ scene, compiled }: { scene: Spec; compiled: { scene_id: string } }): ReactElement {
  const frame = useCurrentFrame();
  const { bundle, theme, assetUrl } = useSceneEnv();
  const { safe, scale, fps, width, height } = useSceneGeometry();
  const f = motionFrames(theme, fps);
  const t = progress(frame, Math.round(f.fast / 2), f.countUp, theme.motion.easing.decelerate);

  const path = bundle.assets[scene.region];
  const src = path === undefined ? null : assetUrl(scene.region, path);

  const [handle] = useState(() => delayRender(`map:${compiled.scene_id}`));
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [fetchFailure, setFetchFailure] = useState<string | null>(null);
  // A missing asset is known during render, so it is derived rather than pushed into state from
  // the effect; the effect owns only the fetch and the delayRender handle.
  const failure = src === null ? `missing asset · ${scene.region}` : fetchFailure;

  useEffect(() => {
    if (src === null) {
      continueRender(handle);
      return;
    }
    let live = true;
    fetch(src)
      .then((response) => (response.ok ? response.json() : Promise.reject(new Error(`HTTP ${response.status}`))))
      .then((topology: Topology) => {
        if (!live) return;
        const object = topology.objects[scene.region] ?? Object.values(topology.objects)[0];
        if (object === undefined) throw new Error("topology has no objects");
        setLoaded({ collection: feature(topology, object) as FeatureCollection });
      })
      .catch((err: unknown) => {
        if (live) setFetchFailure(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (live) continueRender(handle);
      });
    return () => {
      live = false;
    };
  }, [handle, src, scene.region]);

  const title = useFittedText(scene.title.text, "label", safe.width, Math.round(safe.height * 0.14), 2, "paper");
  const mapWidth = safe.width;
  const mapHeight = Math.round(safe.height * 0.66);

  const values = useMemo(
    () => regionValues(scene.data ? bundle.datasets?.[scene.data.dataset_id] : undefined, scene.data?.column ?? null),
    [bundle.datasets, scene.data],
  );
  const maxValue = useMemo(() => Math.max(0, ...values.values()), [values]);

  const projectionKind = scene.projection;
  const drawn = useMemo(() => {
    if (!loaded) return null;
    const projection = PROJECTIONS[projectionKind]().fitSize([mapWidth, mapHeight], loaded.collection);
    const draw = geoPath(projection);
    return loaded.collection.features.map((feat, i) => ({
      key: String(feat.id ?? i),
      d: draw(feat),
      weight: fillWeight(values.get(String(feat.id ?? "")), maxValue),
    }));
  }, [loaded, projectionKind, mapWidth, mapHeight, values, maxValue]);

  if (drawn === null) {
    return (
      <PlaceholderCard
        kind="map"
        title={failure === null ? `loading ${scene.region}` : `${scene.title.text} — ${failure}`}
        sceneId={compiled.scene_id}
        width={width}
        height={height}
        theme={theme}
      />
    );
  }

  return (
    <SceneFrame testId="map" justify="start" notice={refClassification(scene.data, bundle.datasets)}>
      <Lines block={title} style={enter(frame, 0, f.base, theme, 10 * scale)} />
      <svg
        width={mapWidth}
        height={mapHeight}
        viewBox={`0 0 ${mapWidth} ${mapHeight}`}
        style={{ marginTop: 16 * scale, ...enter(frame, Math.round(f.fast / 2), f.base, theme, 14 * scale) }}
        aria-hidden="true"
      >
        {drawn.map((region) =>
          region.d === null ? null : (
            <path
              key={region.key}
              d={region.d}
              fill={region.weight > 0 ? theme.color.accent : theme.color.surface}
              fillOpacity={region.weight > 0 ? region.weight * t : 1}
              stroke={theme.color.rule}
              strokeWidth={Math.max(0.5, 1 * scale)}
            />
          ),
        )}
      </svg>
      <div style={{ marginTop: 8 * scale, color: theme.color.muted, fontSize: Math.max(minTextPx(theme, scale), 20 * scale) }}>
        {scene.data
          ? `${bundle.datasets?.[scene.data.dataset_id]?.label ?? scene.data.dataset_id} · ${values.size} of ${drawn.length} regions`
          : `${drawn.length} regions · ${scene.projection}`}
      </div>
    </SceneFrame>
  );
}
