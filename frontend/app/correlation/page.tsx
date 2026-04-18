"use client";

import { useState, useMemo } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchPriceHistoryBatch } from "@/lib/api";
import { getChartTheme, getBaseLayout, CHART_HEIGHT, heatmapTrace, heatmapHeight, type ChartTheme } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import { ChartCard } from "@/components/ui/chart-card";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const ASSET_CLASSES: Record<string, Record<string, string>> = {
  "US Equities": { SPY: "S&P 500", QQQ: "Nasdaq 100", IWM: "Russell 2000", DIA: "Dow 30", MDY: "S&P 400 Mid" },
  Sectors: {
    XLK: "Technology", XLF: "Financials", XLE: "Energy", XLV: "Healthcare",
    XLI: "Industrials", XLU: "Utilities", XLP: "Staples", XLY: "Discretionary",
    XLC: "Comms", XLB: "Materials", XLRE: "Real Estate",
  },
  "Fixed Income": {
    TLT: "20Y Treasury", IEF: "7-10Y Treasury", SHY: "1-3Y Treasury",
    LQD: "IG Corporate", HYG: "High Yield", TIP: "TIPS", EMB: "EM Bonds",
  },
  Commodities: {
    GLD: "Gold", SLV: "Silver", USO: "Crude Oil", UNG: "Natural Gas",
    DBA: "Agriculture", CPER: "Copper",
  },
  International: {
    EFA: "Developed Intl", EEM: "Emerging Mkts", FXI: "China", EWJ: "Japan", VGK: "Europe",
  },
  "Volatility & Alt": {
    VIXY: "VIX Short-Term", GDX: "Gold Miners", XBI: "Biotech", ARKK: "Innovation",
  },
};

const ALL_TICKERS = Object.values(ASSET_CLASSES).flatMap(cls => Object.keys(cls));
const ALL_ASSETS: Record<string, string> = {};
const TICKER_CLASS: Record<string, string> = {};
for (const [cls, tickers] of Object.entries(ASSET_CLASSES)) {
  for (const [tk, name] of Object.entries(tickers)) {
    ALL_ASSETS[tk] = name;
    TICKER_CLASS[tk] = cls;
  }
}

const CLASS_COLORS: Record<string, string> = {
  "US Equities": "#00d1ff", Sectors: "#3fb950", "Fixed Income": "#f59e0b",
  Commodities: "#a78bfa", International: "#f85149", "Volatility & Alt": "#ec4899",
};

const TABS = [
  "Correlation Matrix",
  "Rolling Correlation",
  "Regime Analysis",
  "Clustering",
  "Breakdown Alerts",
  "PCA / Factor Structure",
];

// ─── Math helpers ─────────────────────────────────────────────

function computeReturns(closes: number[]): number[] {
  return closes.slice(1).map((c, i) => closes[i] > 0 ? (c - closes[i]) / closes[i] : 0);
}

function pearsonCorr(a: number[], b: number[]): number {
  const n = Math.min(a.length, b.length);
  if (n < 10) return 0;
  const xa = a.slice(-n), xb = b.slice(-n);
  const ma = xa.reduce((s, v) => s + v, 0) / n;
  const mb = xb.reduce((s, v) => s + v, 0) / n;
  let num = 0, da = 0, db = 0;
  for (let i = 0; i < n; i++) {
    const ai = xa[i] - ma, bi = xb[i] - mb;
    num += ai * bi; da += ai * ai; db += bi * bi;
  }
  return da > 0 && db > 0 ? num / Math.sqrt(da * db) : 0;
}

function mean(xs: number[]): number {
  if (xs.length === 0) return 0;
  return xs.reduce((s, v) => s + v, 0) / xs.length;
}

function stdev(xs: number[]): number {
  if (xs.length < 2) return 0;
  const m = mean(xs);
  return Math.sqrt(xs.reduce((s, v) => s + (v - m) ** 2, 0) / (xs.length - 1));
}

function median(xs: number[]): number {
  if (xs.length === 0) return 0;
  const s = [...xs].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 === 0 ? (s[mid - 1] + s[mid]) / 2 : s[mid];
}

// ─── Hierarchical (Ward) clustering ───────────────────────────

interface DendroNode {
  id: number;              // leaf index for 0..n-1, cluster index for >=n
  left?: DendroNode;
  right?: DendroNode;
  height: number;           // merge height
  leaves: number[];         // leaf indices in this subtree
  clusterSize: number;
}

interface WardMerge {
  a: number;                // cluster id merged
  b: number;                // cluster id merged
  distance: number;         // merge distance (Ward)
  newId: number;            // new cluster id
  newSize: number;
}

function wardLinkage(distMat: number[][]): { merges: WardMerge[]; root: DendroNode } {
  const n = distMat.length;
  // Each active cluster tracks its members and Ward distance to all other clusters
  const activeIds: number[] = [];
  const memberships: Map<number, number[]> = new Map();
  const sizes: Map<number, number> = new Map();
  const clusterDist: Map<number, Map<number, number>> = new Map();
  const nodes: Map<number, DendroNode> = new Map();

  for (let i = 0; i < n; i++) {
    activeIds.push(i);
    memberships.set(i, [i]);
    sizes.set(i, 1);
    clusterDist.set(i, new Map());
    nodes.set(i, { id: i, height: 0, leaves: [i], clusterSize: 1 });
  }
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      // Ward starts equivalent to Euclidean for singletons when distance is already squared-like
      const d = distMat[i][j];
      clusterDist.get(i)!.set(j, d);
      clusterDist.get(j)!.set(i, d);
    }
  }

  const merges: WardMerge[] = [];
  let nextId = n;

  while (activeIds.length > 1) {
    // Find closest pair among active clusters
    let bestA = -1, bestB = -1, bestD = Infinity;
    for (let i = 0; i < activeIds.length; i++) {
      for (let j = i + 1; j < activeIds.length; j++) {
        const a = activeIds[i], b = activeIds[j];
        const d = clusterDist.get(a)?.get(b) ?? Infinity;
        if (d < bestD) { bestD = d; bestA = a; bestB = b; }
      }
    }
    if (bestA < 0) break;

    // Merge a + b -> new cluster
    const sizeA = sizes.get(bestA)!;
    const sizeB = sizes.get(bestB)!;
    const newSize = sizeA + sizeB;
    const mergedMembers = [...memberships.get(bestA)!, ...memberships.get(bestB)!];
    memberships.set(nextId, mergedMembers);
    sizes.set(nextId, newSize);

    const nodeA = nodes.get(bestA)!;
    const nodeB = nodes.get(bestB)!;
    nodes.set(nextId, {
      id: nextId,
      left: nodeA,
      right: nodeB,
      height: bestD,
      leaves: [...nodeA.leaves, ...nodeB.leaves],
      clusterSize: newSize,
    });

    merges.push({ a: bestA, b: bestB, distance: bestD, newId: nextId, newSize });

    // Compute Ward distance from merged cluster to all other active clusters using Lance-Williams
    const newDistMap = new Map<number, number>();
    for (const c of activeIds) {
      if (c === bestA || c === bestB) continue;
      const dAC = clusterDist.get(bestA)?.get(c) ?? Infinity;
      const dBC = clusterDist.get(bestB)?.get(c) ?? Infinity;
      const dAB = bestD;
      const sizeC = sizes.get(c)!;
      const total = sizeA + sizeB + sizeC;
      // Ward's Lance-Williams: d(AuB, C) = sqrt(((a+c)*dAC^2 + (b+c)*dBC^2 - c*dAB^2) / (a+b+c))
      const d2 = ((sizeA + sizeC) * dAC * dAC + (sizeB + sizeC) * dBC * dBC - sizeC * dAB * dAB) / total;
      const d = Math.sqrt(Math.max(0, d2));
      newDistMap.set(c, d);
      clusterDist.get(c)!.set(nextId, d);
      clusterDist.get(c)!.delete(bestA);
      clusterDist.get(c)!.delete(bestB);
    }
    clusterDist.set(nextId, newDistMap);
    clusterDist.delete(bestA);
    clusterDist.delete(bestB);

    // Remove bestA, bestB from active, add nextId
    const filtered = activeIds.filter(id => id !== bestA && id !== bestB);
    filtered.push(nextId);
    activeIds.length = 0;
    activeIds.push(...filtered);

    nextId++;
  }

  return { merges, root: nodes.get(nextId - 1)! };
}

function cutTree(root: DendroNode, k: number): number[] {
  // Walk down the tree, accepting the top (n-k) splits as cluster boundaries.
  // Assign a cluster label (1..k) to each leaf.
  const nLeaves = root.leaves.length;
  if (k >= nLeaves) {
    // Each leaf its own cluster
    const labels: number[] = new Array(nLeaves).fill(0);
    root.leaves.forEach((leaf, i) => { labels[leaf] = i + 1; });
    return labels;
  }

  // BFS split from root until we have k subtrees
  const clusters: DendroNode[] = [root];
  while (clusters.length < k) {
    // Split the cluster with greatest height
    let bestIdx = -1, bestH = -Infinity;
    for (let i = 0; i < clusters.length; i++) {
      if (clusters[i].left && clusters[i].height > bestH) {
        bestIdx = i;
        bestH = clusters[i].height;
      }
    }
    if (bestIdx === -1) break;
    const c = clusters[bestIdx];
    clusters.splice(bestIdx, 1);
    if (c.left) clusters.push(c.left);
    if (c.right) clusters.push(c.right);
  }

  const labels: number[] = new Array(nLeaves).fill(0);
  clusters.forEach((cluster, cidx) => {
    for (const leaf of cluster.leaves) {
      labels[leaf] = cidx + 1;
    }
  });
  return labels;
}

function leafOrder(root: DendroNode): number[] {
  // In-order traversal gives the dendrogram x-axis order
  const out: number[] = [];
  const walk = (node: DendroNode) => {
    if (!node.left && !node.right) { out.push(node.id); return; }
    if (node.left) walk(node.left);
    if (node.right) walk(node.right);
  };
  walk(root);
  return out;
}

// Draw dendrogram as SVG-style line segments in Plotly
function dendrogramTraces(root: DendroNode, order: number[], height: number, t: ChartTheme) {
  // Place leaves at x = 10, 30, 50, ... (spacing 20)
  const spacing = 20;
  const leafX: Map<number, number> = new Map();
  order.forEach((leafId, i) => leafX.set(leafId, 5 + spacing * i));

  // Walk tree, assign x to internal nodes as midpoint of children
  const nodeX: Map<number, number> = new Map();
  const placeX = (node: DendroNode): number => {
    if (!node.left && !node.right) {
      const x = leafX.get(node.id)!;
      nodeX.set(node.id, x);
      return x;
    }
    const xL = placeX(node.left!);
    const xR = placeX(node.right!);
    const mid = (xL + xR) / 2;
    nodeX.set(node.id, mid);
    return mid;
  };
  placeX(root);

  const traces: Record<string, unknown>[] = [];
  const walk = (node: DendroNode) => {
    if (!node.left || !node.right) return;
    const xL = nodeX.get(node.left.id)!;
    const xR = nodeX.get(node.right.id)!;
    const yChildL = node.left.height;
    const yChildR = node.right.height;
    const yMerge = node.height;

    // U-shape: up from left child, across, down to right child
    traces.push({
      type: "scatter", mode: "lines",
      x: [xL, xL, xR, xR],
      y: [yChildL, yMerge, yMerge, yChildR],
      line: { color: t.accent, width: 1.5 },
      showlegend: false, hoverinfo: "skip",
    });
    walk(node.left);
    walk(node.right);
  };
  walk(root);
  return { traces, leafX };
}

// ─── Jacobi eigendecomposition for correlation matrices ────────

function jacobiEigen(Ain: number[][]): { values: number[]; vectors: number[][] } {
  const n = Ain.length;
  const A = Ain.map(row => [...row]);
  const V: number[][] = Array.from({ length: n }, (_, i) => {
    const r = new Array(n).fill(0); r[i] = 1; return r;
  });
  const maxIter = 100;
  for (let iter = 0; iter < maxIter; iter++) {
    // Find largest off-diagonal
    let p = 0, q = 1, maxOff = 0;
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        if (Math.abs(A[i][j]) > maxOff) { maxOff = Math.abs(A[i][j]); p = i; q = j; }
      }
    }
    if (maxOff < 1e-10) break;
    const diff = A[q][q] - A[p][p];
    let theta: number;
    if (Math.abs(A[p][q]) < 1e-30 * Math.abs(diff)) {
      theta = A[p][q] / diff;
    } else {
      const phi = diff / (2 * A[p][q]);
      theta = 1 / (phi + Math.sign(phi) * Math.sqrt(phi * phi + 1));
    }
    const c = 1 / Math.sqrt(1 + theta * theta);
    const s = theta * c;
    const app = A[p][p], aqq = A[q][q], apq = A[p][q];
    A[p][p] = app - theta * apq;
    A[q][q] = aqq + theta * apq;
    A[p][q] = 0;
    A[q][p] = 0;
    for (let i = 0; i < n; i++) {
      if (i === p || i === q) continue;
      const aip = A[i][p], aiq = A[i][q];
      A[i][p] = c * aip - s * aiq;
      A[p][i] = A[i][p];
      A[i][q] = s * aip + c * aiq;
      A[q][i] = A[i][q];
    }
    for (let i = 0; i < n; i++) {
      const vip = V[i][p], viq = V[i][q];
      V[i][p] = c * vip - s * viq;
      V[i][q] = s * vip + c * viq;
    }
  }
  const values = A.map((_, i) => A[i][i]);
  return { values, vectors: V };
}

function sortEigen(eig: { values: number[]; vectors: number[][] }) {
  const n = eig.values.length;
  const idx = Array.from({ length: n }, (_, i) => i).sort((a, b) => eig.values[b] - eig.values[a]);
  const values = idx.map(i => eig.values[i]);
  const vectors: number[][] = [];
  for (let i = 0; i < n; i++) {
    vectors.push(idx.map(j => eig.vectors[i][j]));
  }
  return { values, vectors };
}

// ═════════════════════════════════════════════════════════════

export default function CorrelationPage() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const [activeTab, setActiveTab] = useState(0);
  const [period, setPeriod] = useState(126);
  const [nClusters, setNClusters] = useState(4);
  const [returns, setReturns] = useState<Record<string, number[]>>({});
  const [dates, setDates] = useState<string[]>([]);

  const load = useMutation({
    mutationFn: async () => {
      const data = await fetchPriceHistoryBatch(ALL_TICKERS, period + 10);
      return data;
    },
    onSuccess: (data) => {
      const ret: Record<string, number[]> = {};
      for (const tk of ALL_TICKERS) {
        const closes = (data[tk] || []).map(d => d.Close);
        if (closes.length > 10) ret[tk] = computeReturns(closes);
      }
      setReturns(ret);
      const firstTk = Object.keys(data)[0];
      if (firstTk) setDates((data[firstTk] || []).slice(1).map(d => d.Date));
    },
  });

  const activeTickers = useMemo(() => ALL_TICKERS.filter(tk => returns[tk]?.length > 0), [returns]);
  const n = activeTickers.length;

  const corrMatrix = useMemo(() => {
    if (n < 2) return null;
    const mat: number[][] = Array.from({ length: n }, () => Array(n).fill(0));
    for (let i = 0; i < n; i++) {
      mat[i][i] = 1;
      for (let j = i + 1; j < n; j++) {
        const c = pearsonCorr(returns[activeTickers[i]], returns[activeTickers[j]]);
        mat[i][j] = c; mat[j][i] = c;
      }
    }
    return mat;
  }, [activeTickers, returns, n]);

  const avgCorr = useMemo(() => {
    if (!corrMatrix || n < 2) return 0;
    let sum = 0, count = 0;
    for (let i = 0; i < n; i++)
      for (let j = i + 1; j < n; j++) { sum += corrMatrix[i][j]; count++; }
    return count > 0 ? sum / count : 0;
  }, [corrMatrix, n]);

  // ─── Tab 3: Clustering data ──────────────────────────────
  const clusterData = useMemo(() => {
    if (!corrMatrix || n < 3) return null;
    // Distance = 1 - |corr|
    const dist: number[][] = corrMatrix.map((row, i) =>
      row.map((v, j) => i === j ? 0 : Math.max(0, 1 - Math.abs(v))),
    );
    const { root } = wardLinkage(dist);
    const order = leafOrder(root);
    const k = Math.max(2, Math.min(nClusters, n));
    const labels = cutTree(root, k);
    return { root, order, labels, k };
  }, [corrMatrix, n, nClusters]);

  // ─── Tab 4: Breakdown alerts ─────────────────────────────
  const alerts = useMemo(() => {
    if (n < 2 || !corrMatrix) return null;
    const spyRet = returns["SPY"];
    if (!spyRet || spyRet.length < 126) return { insufficient: true as const, rows: [] as BreakdownAlert[] };

    // Recent 21d + long-run
    const recentMatrix: number[][] = Array.from({ length: n }, () => Array(n).fill(0));
    const rollingStdCache = new Map<string, { mean: number; std: number }>();
    const rollWindow = 63;

    // Pre-compute 63d rolling correlation stats for each pair
    const out: BreakdownAlert[] = [];
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const ta = activeTickers[i], tb = activeTickers[j];
        const ra = returns[ta], rb = returns[tb];
        const commonN = Math.min(ra.length, rb.length);
        if (commonN < 126) continue;
        const raA = ra.slice(-commonN);
        const rbA = rb.slice(-commonN);
        const recent = pearsonCorr(raA.slice(-21), rbA.slice(-21));
        const longRun = pearsonCorr(raA, rbA);
        recentMatrix[i][j] = recent;
        recentMatrix[j][i] = recent;

        // 63d rolling correlation series
        const key = `${ta}_${tb}`;
        let stats = rollingStdCache.get(key);
        if (!stats) {
          const rollSeries: number[] = [];
          for (let k = rollWindow; k <= commonN; k++) {
            rollSeries.push(pearsonCorr(raA.slice(k - rollWindow, k), rbA.slice(k - rollWindow, k)));
          }
          if (rollSeries.length > 30) {
            const m = mean(rollSeries);
            const s = stdev(rollSeries);
            stats = { mean: m, std: s };
            rollingStdCache.set(key, stats);
          } else {
            continue;
          }
        }
        if (stats.std < 0.01) continue;
        const z = (recent - stats.mean) / stats.std;
        if (!Number.isFinite(z) || Math.abs(z) < 1.5) continue;
        out.push({
          pair: `${ta} / ${tb}`,
          tickerA: ta, tickerB: tb,
          recent, longRun, shift: recent - longRun,
          z,
          signal: z < -1.5 ? "BREAKDOWN" : z > 1.5 ? "SPIKE" : "SHIFT",
          classA: TICKER_CLASS[ta] ?? "",
          classB: TICKER_CLASS[tb] ?? "",
        });
      }
    }
    out.sort((a, b) => Math.abs(b.z) - Math.abs(a.z));
    return { insufficient: false as const, rows: out };
  }, [activeTickers, returns, n, corrMatrix]);

  // ─── Tab 5: PCA ───────────────────────────────────────────
  const pca = useMemo(() => {
    if (n < 3 || !corrMatrix) return null;
    // Standardize returns: use returns directly (centered + scaled)
    const standardized: Record<string, number[]> = {};
    const validTickers: string[] = [];
    for (const tk of activeTickers) {
      const r = returns[tk];
      const s = stdev(r);
      if (s < 1e-9) continue;
      const m = mean(r);
      standardized[tk] = r.map(v => (v - m) / s);
      validTickers.push(tk);
    }
    const k = validTickers.length;
    if (k < 3) return null;
    // Use covariance of standardized returns = correlation matrix
    const cov: number[][] = Array.from({ length: k }, () => Array(k).fill(0));
    const commonN = Math.min(...validTickers.map(tk => standardized[tk].length));
    for (let i = 0; i < k; i++) {
      for (let j = i; j < k; j++) {
        const a = standardized[validTickers[i]].slice(-commonN);
        const b = standardized[validTickers[j]].slice(-commonN);
        let sum = 0;
        for (let x = 0; x < commonN; x++) sum += a[x] * b[x];
        const c = sum / (commonN - 1);
        cov[i][j] = c; cov[j][i] = c;
      }
    }
    const eig = sortEigen(jacobiEigen(cov));
    const total = eig.values.reduce((s, v) => s + Math.max(0, v), 0);
    const varExplained = eig.values.map(v => total > 0 ? (Math.max(0, v) / total) * 100 : 0);
    const cumVar: number[] = [];
    let running = 0;
    for (const v of varExplained) { running += v; cumVar.push(running); }

    const nComponents = Math.min(10, k);
    // loadings[ticker][pc] = eigenvectors[rowTicker][colPC]
    const loadings: Record<string, number[]> = {};
    validTickers.forEach((tk, i) => {
      loadings[tk] = Array.from({ length: Math.min(5, k) }, (_, pc) => eig.vectors[i][pc]);
    });
    // Effective dimension (participation ratio)
    const sumVSq = eig.values.reduce((s, v) => s + v * v, 0);
    const effDim = sumVSq > 0 ? (total * total) / sumVSq : 0;
    return { tickers: validTickers, varExplained, cumVar, loadings, nComponents, effDim };
  }, [activeTickers, returns, n, corrMatrix]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Cross-Asset Correlation</h1>
        <p className="text-text-secondary text-sm mt-1">
          Correlation matrix, rolling analysis, regime breakdown, hierarchical clustering, breakdown alerts, and PCA factor structure across {ALL_TICKERS.length} assets.
        </p>
      </div>

      <div className="card card-compact">
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-text-muted">Period:</span>
            {[30, 60, 90, 126, 252, 504].map(d => (
              <button key={d} onClick={() => setPeriod(d)}
                className={`px-2 py-1 text-xs rounded ${period === d ? "bg-accent text-white" : "text-text-muted hover:bg-surface-alt"}`}>
                {d}d
              </button>
            ))}
          </div>
          <button onClick={() => load.mutate()} disabled={load.isPending}
            className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 transition-colors text-sm">
            {load.isPending ? "Loading..." : "Compute"}
          </button>
          <span className="text-xs text-text-muted">
            Breakdown Alerts + rolling stats need ~126 days of history.
          </span>
        </div>
      </div>

      {load.isPending && (
        <div className="card text-center py-12">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-text-muted mt-3">Fetching {ALL_TICKERS.length} tickers...</p>
        </div>
      )}

      {corrMatrix && n > 2 && (
        <>
          <div className="card card-compact">
            <div className="flex flex-wrap gap-6">
              <Metric label="Assets" value={String(n)} />
              <Metric label="Avg Correlation" value={avgCorr.toFixed(2)} />
              <Metric label="Period" value={`${period} days`} />
            </div>
          </div>

          <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
            {TABS.map((tab, i) => (
              <button key={tab} onClick={() => setActiveTab(i)}
                className={`px-3 py-1.5 text-xs font-semibold rounded-t-md transition-colors whitespace-nowrap ${
                  activeTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>
                {tab}
              </button>
            ))}
          </div>

          {/* Tab 0: Matrix */}
          {activeTab === 0 && (
            <ChartCard height={heatmapHeight(n, { compact: n > 15, padding: 120 })}>
              <Plot data={[{
                ...heatmapTrace(t, "correlation", { colorbarTitle: "Corr" }),
                z: corrMatrix,
                x: activeTickers,
                y: activeTickers,
                zmid: 0, zmin: -1, zmax: 1,
                text: corrMatrix.map(row => row.map(v => v.toFixed(2))),
                hovertemplate: "%{x} vs %{y}: %{z:.2f}<extra></extra>",
              }]}
                layout={{
                  height: heatmapHeight(n, { compact: n > 15, padding: 120 }), ...L,
                  margin: { l: 70, r: 40, t: 10, b: 80 },
                  xaxis: { tickangle: -45, tickfont: { size: 9, color: t.text }, gridcolor: t.grid },
                  yaxis: { autorange: "reversed", tickfont: { size: 9, color: t.text }, gridcolor: t.grid },
                }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            </ChartCard>
          )}

          {/* Tab 1: Rolling Correlation */}
          {activeTab === 1 && (
            <ChartCard
              subtitle="63-day rolling correlation of each asset vs SPY."
              height={CHART_HEIGHT.tall}
            >
              {(() => {
                const spyRet = returns["SPY"];
                const window = 63;
                if (!spyRet || spyRet.length < window) return <p className="text-sm text-text-muted">Need SPY data with at least {window} observations.</p>;
                const tickers = activeTickers.filter(tk => tk !== "SPY" && returns[tk]?.length >= window);
                const rollingData = tickers.map(tk => {
                  const ret = returns[tk];
                  const nn = Math.min(ret.length, spyRet.length);
                  const a = spyRet.slice(-nn), b = ret.slice(-nn);
                  const rolling: number[] = [];
                  for (let i = window; i <= nn; i++) {
                    rolling.push(pearsonCorr(a.slice(i - window, i), b.slice(i - window, i)));
                  }
                  return { ticker: tk, rolling };
                });
                const rollingDates = dates.slice(window);
                return (
                  <Plot data={rollingData.map(d => ({
                    x: rollingDates.slice(-d.rolling.length),
                    y: d.rolling,
                    type: "scatter" as const, mode: "lines" as const,
                    name: d.ticker,
                    line: { width: 1.5, color: CLASS_COLORS[TICKER_CLASS[d.ticker]] ?? t.muted },
                  }))}
                    layout={{
                      height: CHART_HEIGHT.tall, ...L,
                      yaxis: { title: { text: "Correlation vs SPY" }, gridcolor: t.grid, range: [-1, 1] },
                      xaxis: { gridcolor: t.grid },
                      hovermode: "x unified",
                      legend: { orientation: "h", y: -0.15 },
                      shapes: [{ type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, width: 1, dash: "dash" } }],
                    }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                );
              })()}
            </ChartCard>
          )}

          {/* Tab 2: Regime Analysis */}
          {activeTab === 2 && (
            <ChartCard
              subtitle="Average correlation vs SPY by asset class, split by calm vs stress days (|SPY return| median)."
              height={CHART_HEIGHT.normal + 20}
            >
              {(() => {
                const spyRet = returns["SPY"];
                if (!spyRet) return null;
                const absRet = spyRet.map(r => Math.abs(r));
                const med = median(absRet);
                const classCorrs: { cls: string; calm: number; stress: number }[] = [];
                for (const cls of Object.keys(ASSET_CLASSES)) {
                  const tickers = Object.keys(ASSET_CLASSES[cls]).filter(tk => returns[tk]?.length > 0 && tk !== "SPY");
                  if (tickers.length === 0) continue;
                  let calmSum = 0, stressSum = 0, calmN = 0, stressN = 0;
                  for (const tk of tickers) {
                    const ret = returns[tk];
                    const nn = Math.min(ret.length, spyRet.length);
                    const calmA: number[] = [], calmB: number[] = [], stressA: number[] = [], stressB: number[] = [];
                    for (let i = 0; i < nn; i++) {
                      if (absRet[i] <= med) { calmA.push(spyRet[i]); calmB.push(ret[i]); }
                      else { stressA.push(spyRet[i]); stressB.push(ret[i]); }
                    }
                    if (calmA.length > 5) { calmSum += pearsonCorr(calmA, calmB); calmN++; }
                    if (stressA.length > 5) { stressSum += pearsonCorr(stressA, stressB); stressN++; }
                  }
                  classCorrs.push({
                    cls,
                    calm: calmN > 0 ? calmSum / calmN : 0,
                    stress: stressN > 0 ? stressSum / stressN : 0,
                  });
                }
                return (
                  <Plot data={[
                    { x: classCorrs.map(c => c.cls), y: classCorrs.map(c => c.calm), type: "bar" as const, name: "Calm Days", marker: { color: t.gain } },
                    { x: classCorrs.map(c => c.cls), y: classCorrs.map(c => c.stress), type: "bar" as const, name: "Stress Days", marker: { color: t.loss } },
                  ]}
                    layout={{
                      height: CHART_HEIGHT.normal + 20, ...L, barmode: "group",
                      yaxis: { title: { text: "Avg Corr vs SPY" }, gridcolor: t.grid, range: [-0.5, 1] },
                      xaxis: { gridcolor: t.grid },
                      legend: { orientation: "h", y: -0.2 },
                      margin: { l: 60, r: 20, t: 20, b: 80 },
                    }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                );
              })()}
            </ChartCard>
          )}

          {/* Tab 3: Clustering */}
          {activeTab === 3 && clusterData && (
            <ClusteringView
              activeTickers={activeTickers}
              corrMatrix={corrMatrix}
              root={clusterData.root}
              order={clusterData.order}
              labels={clusterData.labels}
              nClusters={clusterData.k}
              setNClusters={setNClusters}
              t={t}
              L={L}
            />
          )}

          {/* Tab 4: Breakdown Alerts */}
          {activeTab === 4 && alerts && (
            <BreakdownView alerts={alerts} returns={returns} dates={dates} t={t} L={L} />
          )}

          {/* Tab 5: PCA */}
          {activeTab === 5 && pca && (
            <PcaView pca={pca} t={t} L={L} />
          )}

          {activeTab === 5 && !pca && (
            <div className="card text-sm text-text-muted py-6">Need at least 3 assets for PCA decomposition.</div>
          )}
        </>
      )}
    </div>
  );
}

// ═════════════════════════════════════════════════════════════
// Tab 3 — Clustering view
// ═════════════════════════════════════════════════════════════

function ClusteringView({
  activeTickers, corrMatrix, root, order, labels, nClusters, setNClusters, t, L,
}: {
  activeTickers: string[];
  corrMatrix: number[][];
  root: DendroNode;
  order: number[];
  labels: number[];
  nClusters: number;
  setNClusters: (n: number) => void;
  t: ChartTheme;
  L: ReturnType<typeof getBaseLayout>;
}) {
  const n = activeTickers.length;
  const dendroResult = useMemo(() => dendrogramTraces(root, order, CHART_HEIGHT.tall, t), [root, order, t]);
  const leafXEntries = [...dendroResult.leafX.entries()].sort((a, b) => a[1] - b[1]);
  const tickVals = leafXEntries.map(([, x]) => x);
  const tickText = leafXEntries.map(([leafId]) => activeTickers[leafId]);

  // Cluster composition
  const clusterRows = useMemo(() => {
    const rows: { cluster: number; members: string[]; size: number; intra: number; classes: string[] }[] = [];
    for (let cid = 1; cid <= nClusters; cid++) {
      const idx = labels.map((lbl, i) => lbl === cid ? i : -1).filter(i => i >= 0);
      if (idx.length === 0) continue;
      const members = idx.map(i => activeTickers[i]);
      let sum = 0, count = 0;
      for (let i = 0; i < idx.length; i++) {
        for (let j = i + 1; j < idx.length; j++) {
          sum += corrMatrix[idx[i]][idx[j]];
          count++;
        }
      }
      const intra = count > 0 ? sum / count : 1.0;
      const classes = Array.from(new Set(members.map(m => TICKER_CLASS[m] ?? "?"))).sort();
      rows.push({ cluster: cid, members, size: idx.length, intra, classes });
    }
    return rows;
  }, [activeTickers, corrMatrix, labels, nClusters]);

  // Reordered correlation matrix
  const reordered = useMemo(() => {
    const ordered = order.map(i => activeTickers[i]);
    const z: number[][] = order.map(i => order.map(j => corrMatrix[i][j]));
    return { ordered, z };
  }, [activeTickers, corrMatrix, order]);

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <p className="text-xs text-text-muted mb-2">
          Hierarchical Ward clustering on 1 − |correlation| distance. Assets that cluster together move together — use this to identify redundant positions and find diversifiers.
        </p>
        <div className="flex items-center gap-3">
          <label className="text-xs text-text-muted">Number of clusters: <b className="text-text">{nClusters}</b></label>
          <input
            type="range" min={2} max={Math.min(8, n)} value={nClusters}
            onChange={e => setNClusters(parseInt(e.target.value))}
            className="flex-1 max-w-[260px]"
          />
        </div>
      </div>

      <ChartCard height={CHART_HEIGHT.tall}>
        <Plot
          data={dendroResult.traces as never}
          layout={{
            height: CHART_HEIGHT.tall, ...L,
            title: { text: `Hierarchical Clustering (Ward Linkage, ${nClusters} clusters)`, font: { size: 14, color: t.text } },
            xaxis: { tickvals: tickVals, ticktext: tickText, tickangle: -45, tickfont: { size: 10, color: t.text }, showgrid: false },
            yaxis: { title: { text: "Distance (1 − |correlation|)" }, gridcolor: t.grid, zeroline: false },
            margin: { l: 60, r: 20, t: 40, b: 80 },
            showlegend: false,
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </ChartCard>

      <div className="card">
        <div className="font-semibold text-sm mb-2">Cluster Composition</div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs font-data">
            <thead className="border-b border-border text-text-muted">
              <tr>
                <th className="text-left py-1.5 px-2">Cluster</th>
                <th className="text-left py-1.5 px-2">Size</th>
                <th className="text-left py-1.5 px-2">Avg Intra-Corr</th>
                <th className="text-left py-1.5 px-2">Members</th>
                <th className="text-left py-1.5 px-2">Asset Classes</th>
              </tr>
            </thead>
            <tbody>
              {clusterRows.map(r => (
                <tr key={r.cluster} className="border-b border-border/50 hover:bg-surface-alt">
                  <td className="py-1 px-2 font-bold">#{r.cluster}</td>
                  <td className="py-1 px-2">{r.size}</td>
                  <td className="py-1 px-2">{r.intra.toFixed(3)}</td>
                  <td className="py-1 px-2">{r.members.join(", ")}</td>
                  <td className="py-1 px-2 text-text-muted">{r.classes.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <ChartCard
        title="Correlation Matrix (Reordered by Cluster)"
        height={heatmapHeight(n, { compact: n > 15, padding: 120 })}
      >
        <Plot
          data={[{
            ...heatmapTrace(t, "correlation", { colorbarTitle: "Corr" }),
            z: reordered.z,
            x: reordered.ordered, y: reordered.ordered,
            zmid: 0, zmin: -1, zmax: 1,
            text: reordered.z.map(row => row.map(v => v.toFixed(2))),
            hovertemplate: "%{x} vs %{y}: %{z:.2f}<extra></extra>",
          }]}
          layout={{
            height: heatmapHeight(n, { compact: n > 15, padding: 120 }), ...L,
            margin: { l: 70, r: 40, t: 10, b: 80 },
            xaxis: { tickangle: -45, tickfont: { size: 9, color: t.text }, gridcolor: t.grid },
            yaxis: { autorange: "reversed", tickfont: { size: 9, color: t.text }, gridcolor: t.grid },
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </ChartCard>
    </div>
  );
}

// ═════════════════════════════════════════════════════════════
// Tab 4 — Breakdown Alerts
// ═════════════════════════════════════════════════════════════

interface BreakdownAlert {
  pair: string;
  tickerA: string; tickerB: string;
  recent: number; longRun: number; shift: number;
  z: number;
  signal: "BREAKDOWN" | "SPIKE" | "SHIFT";
  classA: string; classB: string;
}

function BreakdownView({
  alerts, returns, dates, t, L,
}: {
  alerts: { insufficient: true; rows: BreakdownAlert[] } | { insufficient: false; rows: BreakdownAlert[] };
  returns: Record<string, number[]>;
  dates: string[];
  t: ChartTheme;
  L: ReturnType<typeof getBaseLayout>;
}) {
  // Rules of Hooks: all hooks must run before any conditional early-return.
  const rollingWindow = 63;
  const top = !alerts.insufficient && alerts.rows.length > 0 ? alerts.rows[0] : null;
  const detailRolling = useMemo(() => {
    if (!top) return null;
    const ra = returns[top.tickerA];
    const rb = returns[top.tickerB];
    if (!ra || !rb) return null;
    const nn = Math.min(ra.length, rb.length);
    const a = ra.slice(-nn), b = rb.slice(-nn);
    const rolling: number[] = [];
    for (let i = rollingWindow; i <= nn; i++) {
      rolling.push(pearsonCorr(a.slice(i - rollingWindow, i), b.slice(i - rollingWindow, i)));
    }
    return rolling;
  }, [returns, top]);

  if (alerts.insufficient) {
    return (
      <div className="card text-sm text-text-muted py-6">
        Need at least 6 months of data for breakdown detection.
      </div>
    );
  }
  if (alerts.rows.length === 0) {
    return (
      <div className="card text-sm py-6" style={{ color: t.gain }}>
        ✓ No correlation breakdowns detected. All pairs within normal range.
      </div>
    );
  }

  const breakdowns = alerts.rows.filter(a => a.signal === "BREAKDOWN").length;
  const spikes = alerts.rows.filter(a => a.signal === "SPIKE").length;
  // `top` is guaranteed non-null past these early returns — narrow for later usage.
  const topAlert = alerts.rows[0];

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <p className="text-xs text-text-muted mb-3">
          Flags pairs whose recent (21D) correlation deviates significantly from their 63D rolling history (|Z| ≥ 1.5).
        </p>
        <div className="flex flex-wrap gap-6">
          <Metric label="Total Alerts" value={String(alerts.rows.length)} />
          <Metric label="Breakdowns" value={String(breakdowns)} deltaType="loss" />
          <Metric label="Spikes" value={String(spikes)} deltaType="gain" />
        </div>
      </div>

      <div className="card">
        <div className="font-semibold text-sm mb-2">Alert Details (sorted by |Z|)</div>
        <div className="overflow-x-auto max-h-[520px]">
          <table className="w-full text-xs font-data">
            <thead className="border-b border-border text-text-muted sticky top-0 bg-surface">
              <tr>
                <th className="text-left py-1.5 px-2">Pair</th>
                <th className="text-left py-1.5 px-2">Signal</th>
                <th className="text-right py-1.5 px-2">Recent (21D)</th>
                <th className="text-right py-1.5 px-2">Long-Run</th>
                <th className="text-right py-1.5 px-2">Shift</th>
                <th className="text-right py-1.5 px-2">Z-Score</th>
                <th className="text-left py-1.5 px-2">Class A</th>
                <th className="text-left py-1.5 px-2">Class B</th>
              </tr>
            </thead>
            <tbody>
              {alerts.rows.map((r, i) => (
                <tr key={i} className="border-b border-border/50 hover:bg-surface-alt">
                  <td className="py-1 px-2 font-semibold">{r.pair}</td>
                  <td className="py-1 px-2">
                    <span style={{ color: r.signal === "BREAKDOWN" ? t.loss : r.signal === "SPIKE" ? t.gain : t.spot }}>
                      {r.signal}
                    </span>
                  </td>
                  <td className="py-1 px-2 text-right">{r.recent >= 0 ? "+" : ""}{r.recent.toFixed(3)}</td>
                  <td className="py-1 px-2 text-right">{r.longRun >= 0 ? "+" : ""}{r.longRun.toFixed(3)}</td>
                  <td className="py-1 px-2 text-right">{r.shift >= 0 ? "+" : ""}{r.shift.toFixed(3)}</td>
                  <td className="py-1 px-2 text-right">{r.z >= 0 ? "+" : ""}{r.z.toFixed(1)}</td>
                  <td className="py-1 px-2 text-text-muted">{r.classA}</td>
                  <td className="py-1 px-2 text-text-muted">{r.classB}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {detailRolling && detailRolling.length > 0 && (
        <ChartCard
          title={`Top Alert Detail — ${topAlert.pair} · ${topAlert.signal} (Z = ${topAlert.z >= 0 ? "+" : ""}${topAlert.z.toFixed(1)})`}
          height={CHART_HEIGHT.normal}
        >
          <Plot
            data={[{
              x: dates.slice(-detailRolling.length),
              y: detailRolling,
              type: "scatter", mode: "lines",
              line: { color: t.accent, width: 2 },
              name: "63D Rolling Corr",
            }]}
            layout={{
              height: CHART_HEIGHT.normal, ...L,
              yaxis: { title: { text: "Correlation" }, gridcolor: t.grid, range: [-1.05, 1.05] },
              xaxis: { gridcolor: t.grid },
              margin: { l: 60, r: 20, t: 20, b: 40 },
              shapes: [
                { type: "line", y0: topAlert.longRun, y1: topAlert.longRun, x0: 0, x1: 1, xref: "paper", line: { color: t.spot, dash: "dot" } },
                { type: "line", y0: topAlert.recent, y1: topAlert.recent, x0: 0, x1: 1, xref: "paper", line: { color: t.loss, dash: "dash" } },
              ],
              annotations: [
                { xref: "paper", x: 1.0, y: topAlert.longRun, text: `Long-run: ${topAlert.longRun.toFixed(3)}`, showarrow: false, font: { size: 9, color: t.spot }, xanchor: "right", yshift: 10 },
                { xref: "paper", x: 1.0, y: topAlert.recent, text: `Recent: ${topAlert.recent.toFixed(3)}`, showarrow: false, font: { size: 9, color: t.loss }, xanchor: "right", yshift: -10 },
              ],
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </ChartCard>
      )}
    </div>
  );
}

// ═════════════════════════════════════════════════════════════
// Tab 5 — PCA / Factor Structure
// ═════════════════════════════════════════════════════════════

function PcaView({
  pca, t, L,
}: {
  pca: {
    tickers: string[];
    varExplained: number[];
    cumVar: number[];
    loadings: Record<string, number[]>;
    nComponents: number;
    effDim: number;
  };
  t: ChartTheme;
  L: ReturnType<typeof getBaseLayout>;
}) {
  const nShow = Math.min(5, pca.nComponents);
  const pcLabels = Array.from({ length: pca.nComponents }, (_, i) => `PC${i + 1}`);

  // Loadings sorted by PC1
  const sortedTickers = useMemo(() => {
    return [...pca.tickers].sort((a, b) => (pca.loadings[b]?.[0] ?? 0) - (pca.loadings[a]?.[0] ?? 0));
  }, [pca.tickers, pca.loadings]);

  const loadingsGrid = sortedTickers.map(tk =>
    Array.from({ length: nShow }, (_, i) => pca.loadings[tk]?.[i] ?? 0),
  );

  const pcsFor80 = (() => {
    for (let i = 0; i < pca.cumVar.length; i++) if (pca.cumVar[i] >= 80) return i + 1;
    return pca.cumVar.length;
  })();

  // Factor interpretation for first 3 PCs
  const factorInterps = useMemo(() => {
    const interps: { pc: string; variance: number; topPos: [string, number][]; topNeg: [string, number][]; eqVsBd?: { eq: number; bd: number; riskOnOff: boolean } }[] = [];
    for (let pc = 0; pc < Math.min(3, nShow); pc++) {
      const entries: [string, number][] = pca.tickers.map(tk => [tk, pca.loadings[tk]?.[pc] ?? 0]);
      entries.sort((a, b) => b[1] - a[1]);
      const topPos = entries.slice(0, 3);
      const topNeg = entries.slice(-3).reverse();

      let eqVsBd: { eq: number; bd: number; riskOnOff: boolean } | undefined;
      if (pc === 0) {
        const eqLoads = entries.filter(([tk]) => TICKER_CLASS[tk] === "US Equities" || TICKER_CLASS[tk] === "Sectors").map(([, v]) => v);
        const bdLoads = entries.filter(([tk]) => TICKER_CLASS[tk] === "Fixed Income").map(([, v]) => v);
        if (eqLoads.length > 0 && bdLoads.length > 0) {
          const eq = eqLoads.reduce((s, v) => s + v, 0) / eqLoads.length;
          const bd = bdLoads.reduce((s, v) => s + v, 0) / bdLoads.length;
          eqVsBd = { eq, bd, riskOnOff: eq * bd < 0 };
        }
      }
      interps.push({
        pc: `PC${pc + 1}`,
        variance: pca.varExplained[pc],
        topPos, topNeg,
        eqVsBd,
      });
    }
    return interps;
  }, [pca, nShow]);

  // Asset map (PC1 vs PC2)
  const assetMap = useMemo(() => {
    const byClass: Record<string, { tickers: string[]; x: number[]; y: number[] }> = {};
    for (const tk of pca.tickers) {
      const cls = TICKER_CLASS[tk] ?? "?";
      if (!byClass[cls]) byClass[cls] = { tickers: [], x: [], y: [] };
      byClass[cls].tickers.push(tk);
      byClass[cls].x.push(pca.loadings[tk]?.[0] ?? 0);
      byClass[cls].y.push(pca.loadings[tk]?.[1] ?? 0);
    }
    return byClass;
  }, [pca.tickers, pca.loadings]);

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <p className="text-xs text-text-muted">
          Principal Component Analysis reveals the hidden factors driving asset returns. PC1 is typically &quot;risk-on/risk-off&quot;, PC2 is often rates or sector rotation.
        </p>
      </div>

      {/* Scree plot */}
      <ChartCard height={CHART_HEIGHT.normal + 40}>
        <Plot
          data={[
            {
              type: "bar",
              x: pcLabels, y: pca.varExplained.slice(0, pca.nComponents),
              name: "Individual", marker: { color: t.accent },
              text: pca.varExplained.slice(0, pca.nComponents).map(v => `${v.toFixed(1)}%`),
              textposition: "outside", yaxis: "y1",
            },
            {
              type: "scatter", mode: "lines+markers",
              x: pcLabels, y: pca.cumVar.slice(0, pca.nComponents),
              name: "Cumulative",
              line: { color: t.spot, width: 2 }, marker: { size: 7 },
              yaxis: "y2",
            },
          ]}
          layout={{
            height: CHART_HEIGHT.normal + 40, ...L,
            title: { text: "Scree Plot — Variance Explained by Principal Component", font: { size: 14, color: t.text } },
            yaxis: { title: { text: "Individual (%)" }, gridcolor: t.grid, side: "left" },
            yaxis2: { title: { text: "Cumulative (%)" }, overlaying: "y", side: "right", range: [0, 105] },
            xaxis: { gridcolor: t.grid },
            margin: { l: 60, r: 60, t: 40, b: 40 },
            legend: { orientation: "h", y: -0.15 },
            shapes: [{ type: "line", x0: 0, x1: 1, xref: "paper", y0: 80, y1: 80, yref: "y2", line: { color: t.muted, dash: "dash" } }],
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </ChartCard>

      <div className="card card-compact">
        <div className="flex flex-wrap gap-6">
          <Metric label="PC1 Explains" value={`${pca.varExplained[0].toFixed(1)}%`} />
          <Metric label="PC1+PC2" value={`${pca.cumVar[1]?.toFixed(1) ?? "—"}%`} />
          <Metric label="PCs for 80%" value={String(pcsFor80)} />
          <Metric label="Effective Dimension" value={pca.effDim.toFixed(1)} />
        </div>
      </div>

      {/* Factor loadings heatmap */}
      <ChartCard
        title="Factor Loadings"
        subtitle="How each asset loads onto the principal components. High absolute loading = strong exposure to that factor."
        height={heatmapHeight(sortedTickers.length, { compact: sortedTickers.length > 15 })}
      >
        <Plot
          data={[{
            ...heatmapTrace(t, "correlation", { colorbarTitle: "Loading" }),
            z: loadingsGrid,
            x: pcLabels.slice(0, nShow),
            y: sortedTickers,
            zmid: 0,
            text: loadingsGrid.map(row => row.map(v => v.toFixed(2))),
            hovertemplate: "%{y} on %{x}: %{z:.3f}<extra></extra>",
          }]}
          layout={{
            height: heatmapHeight(sortedTickers.length, { compact: sortedTickers.length > 15 }), ...L,
            margin: { l: 70, r: 40, t: 20, b: 40 },
            xaxis: { gridcolor: t.grid, tickfont: { size: 10, color: t.text } },
            yaxis: { autorange: "reversed", gridcolor: t.grid, tickfont: { size: 9, color: t.text } },
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </ChartCard>

      {/* Factor interpretation */}
      <div className="card space-y-3">
        <div className="font-semibold text-sm">Factor Interpretation</div>
        {factorInterps.map((interp, i) => (
          <details key={interp.pc} open={i === 0} className="border border-border rounded">
            <summary className="cursor-pointer px-3 py-2 text-sm font-semibold bg-surface-alt/50">
              {interp.pc} — {interp.variance.toFixed(1)}% of variance
            </summary>
            <div className="p-3 text-xs space-y-1">
              <div>
                <b className="text-gain">Positive loadings:</b>{" "}
                {interp.topPos.map(([tk, v]) => `${tk} (${ALL_ASSETS[tk] ?? tk}: ${v >= 0 ? "+" : ""}${v.toFixed(2)})`).join(", ")}
              </div>
              <div>
                <b className="text-loss">Negative loadings:</b>{" "}
                {interp.topNeg.map(([tk, v]) => `${tk} (${ALL_ASSETS[tk] ?? tk}: ${v >= 0 ? "+" : ""}${v.toFixed(2)})`).join(", ")}
              </div>
              {interp.eqVsBd && (
                <div className="mt-2 p-2 rounded" style={{ background: "rgba(88,166,255,0.08)" }}>
                  {interp.eqVsBd.riskOnOff ? (
                    <>PC1 appears to be a <b>risk-on/risk-off</b> factor (equities {interp.eqVsBd.eq >= 0 ? "+" : ""}{interp.eqVsBd.eq.toFixed(2)} vs bonds {interp.eqVsBd.bd >= 0 ? "+" : ""}{interp.eqVsBd.bd.toFixed(2)}).</>
                  ) : (
                    <>PC1 is a <b>level</b> factor — equities and bonds load in the same direction ({interp.eqVsBd.eq >= 0 ? "+" : ""}{interp.eqVsBd.eq.toFixed(2)}, {interp.eqVsBd.bd >= 0 ? "+" : ""}{interp.eqVsBd.bd.toFixed(2)}).</>
                  )}
                </div>
              )}
            </div>
          </details>
        ))}
      </div>

      {/* Asset map */}
      <ChartCard
        title="Asset Map (PC1 vs PC2)"
        subtitle="Assets close together in PC space have similar risk profiles. Distance = dissimilarity."
        height={CHART_HEIGHT.tall + 60}
      >
        <Plot
          data={Object.entries(assetMap).map(([cls, grp]) => ({
            x: grp.x, y: grp.y,
            mode: "markers+text", type: "scatter",
            name: cls,
            marker: { size: 14, color: CLASS_COLORS[cls] ?? t.muted, line: { color: "#fff", width: 1 } },
            text: grp.tickers, textposition: "top center",
            textfont: { size: 10, color: t.text },
            hovertemplate: "%{text}<br>PC1: %{x:.3f}<br>PC2: %{y:.3f}<extra></extra>",
          }))}
          layout={{
            height: CHART_HEIGHT.tall + 60, ...L,
            title: { text: "Asset Map — PC1 (Risk) vs PC2 (Rotation)", font: { size: 14, color: t.text } },
            xaxis: { title: { text: `PC1 (${pca.varExplained[0].toFixed(1)}% var)` }, gridcolor: t.grid, zeroline: true, zerolinecolor: t.muted },
            yaxis: { title: { text: `PC2 (${pca.varExplained[1]?.toFixed(1) ?? "—"}% var)` }, gridcolor: t.grid, zeroline: true, zerolinecolor: t.muted },
            legend: { orientation: "h", y: -0.12 },
            margin: { l: 60, r: 20, t: 40, b: 60 },
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </ChartCard>
    </div>
  );
}
