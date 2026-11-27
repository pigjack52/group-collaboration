import { Fragment, Lifeline, Message, SequenceDiagramData } from '../types/sequenceDiagram';

export interface LayoutWeights {
  /** w1: Fragment 水平跨度權重 */
  w1: number;
  /** w2: Message 發送與接收者距離權重 */
  w2: number;
  /** w3: Fragment 包入多餘 Lifeline 的懲罰權重 */
  w3: number;
}

export interface LayoutCostBreakdown {
  fragmentWidth: number;
  messageDistance: number;
  unnecessaryLifelineCoverage: number;
  totalCost: number;
}

export interface LifelineLayoutOptions {
  startX?: number;
  spacing?: number;
  fragmentPaddingX?: number;
  weights?: LayoutWeights;
}

export interface LifelineLayoutResult {
  diagram: SequenceDiagramData;
  order: string[];
  cost: LayoutCostBreakdown;
}

export const DEFAULT_LAYOUT_WEIGHTS: LayoutWeights = {
  w1: 2,
  w2: 1,
  w3: 3,
};

const DEFAULT_START_X = 140;
const DEFAULT_SPACING = 200;
const DEFAULT_FRAGMENT_PADDING_X = 76;

/**
 * 取得指定 Fragment 所涵蓋的 Lifeline ID 集合
 * 依據 message.fragmentId 或 fragment.interactions 對應出參與的發送/接收物件
 */
export function getFragmentLifelineIds(
  fragment: Fragment,
  messages: Message[],
): Set<string> {
  const participantIds = new Set<string>();
  const interactionSet = new Set(fragment.interactions ?? []);

  for (const msg of messages) {
    if (msg.fragmentId === fragment.id || interactionSet.has(msg.id)) {
      participantIds.add(msg.fromId);
      participantIds.add(msg.toId);
    }
  }

  return participantIds;
}

/**
 * 計算特定 Lifeline 排列順序的成本函數：
 * Cost = w1 * FragmentWidth + w2 * MessageDistance + w3 * UnnecessaryLifelineCoverage
 */
export function evaluateLayoutCost(
  order: string[],
  diagram: SequenceDiagramData,
  weights: LayoutWeights = DEFAULT_LAYOUT_WEIGHTS,
): LayoutCostBreakdown {
  const indexMap = new Map<string, number>(order.map((id, idx) => [id, idx]));

  let messageDistance = 0;
  for (const msg of diagram.messages) {
    const fromIdx = indexMap.get(msg.fromId);
    const toIdx = indexMap.get(msg.toId);
    if (fromIdx !== undefined && toIdx !== undefined) {
      messageDistance += Math.abs(fromIdx - toIdx);
    }
  }

  let fragmentWidth = 0;
  let unnecessaryLifelineCoverage = 0;

  for (const fragment of diagram.fragments) {
    const participants = getFragmentLifelineIds(fragment, diagram.messages);
    const indices: number[] = [];

    for (const id of participants) {
      const idx = indexMap.get(id);
      if (idx !== undefined) {
        indices.push(idx);
      }
    }

    if (indices.length === 0) {
      continue;
    }

    const minIdx = Math.min(...indices);
    const maxIdx = Math.max(...indices);
    fragmentWidth += maxIdx - minIdx;

    for (let i = minIdx; i <= maxIdx; i++) {
      if (!participants.has(order[i])) {
        unnecessaryLifelineCoverage += 1;
      }
    }
  }

  const totalCost =
    weights.w1 * fragmentWidth +
    weights.w2 * messageDistance +
    weights.w3 * unnecessaryLifelineCoverage;

  return {
    fragmentWidth,
    messageDistance,
    unnecessaryLifelineCoverage,
    totalCost,
  };
}

/**
 * 建立初始貪婪排列（Greedy）：
 * 依據訊息互動次數與共同參與 Fragment 的親和度，依序選擇與已排列群組關聯最強的 Lifeline
 */
function buildGreedyOrder(diagram: SequenceDiagramData): string[] {
  const ids = diagram.lifelines.map((l) => l.id);
  if (ids.length <= 2) {
    return ids;
  }

  const affinity = new Map<string, Map<string, number>>();
  for (const id of ids) {
    affinity.set(id, new Map());
  }

  const addAffinity = (a: string, b: string, score: number) => {
    if (a === b) return;
    const mapA = affinity.get(a);
    const mapB = affinity.get(b);
    if (mapA && mapB) {
      mapA.set(b, (mapA.get(b) ?? 0) + score);
      mapB.set(a, (mapB.get(a) ?? 0) + score);
    }
  };

  for (const msg of diagram.messages) {
    addAffinity(msg.fromId, msg.toId, 1);
  }

  for (const fragment of diagram.fragments) {
    const participants = Array.from(getFragmentLifelineIds(fragment, diagram.messages));
    for (let i = 0; i < participants.length; i++) {
      for (let j = i + 1; j < participants.length; j++) {
        addAffinity(participants[i], participants[j], 2);
      }
    }
  }

  let startId = ids[0];
  let maxDegree = -1;
  for (const id of ids) {
    let degree = 0;
    affinity.get(id)?.forEach((v) => {
      degree += v;
    });
    if (degree > maxDegree) {
      maxDegree = degree;
      startId = id;
    }
  }

  const placed: string[] = [startId];
  const remaining = new Set(ids.filter((id) => id !== startId));

  while (remaining.size > 0) {
    let bestCandidate: string | null = null;
    let bestScore = -1;

    for (const candidate of remaining) {
      let score = 0;
      const candidateAffinity = affinity.get(candidate);
      for (const p of placed) {
        score += candidateAffinity?.get(p) ?? 0;
      }
      if (score > bestScore) {
        bestScore = score;
        bestCandidate = candidate;
      }
    }

    const chosen = bestCandidate ?? Array.from(remaining)[0];
    placed.push(chosen);
    remaining.delete(chosen);
  }

  return placed;
}

/**
 * 使用 Adjacent Swap 局部搜尋進一步降低成本
 */
function optimizeByAdjacentSwap(
  initialOrder: string[],
  diagram: SequenceDiagramData,
  weights: LayoutWeights,
): { order: string[]; cost: LayoutCostBreakdown } {
  let currentOrder = [...initialOrder];
  let currentCost = evaluateLayoutCost(currentOrder, diagram, weights);
  let improved = true;

  while (improved) {
    improved = false;
    for (let i = 0; i < currentOrder.length - 1; i++) {
      const candidateOrder = [...currentOrder];
      [candidateOrder[i], candidateOrder[i + 1]] = [
        candidateOrder[i + 1],
        candidateOrder[i],
      ];

      const candidateCost = evaluateLayoutCost(candidateOrder, diagram, weights);
      if (candidateCost.totalCost < currentCost.totalCost) {
        currentOrder = candidateOrder;
        currentCost = candidateCost;
        improved = true;
      }
    }
  }

  return { order: currentOrder, cost: currentCost };
}

/**
 * 執行 Lifeline 自動排列演算法（Greedy + Adjacent Swap），
 * 回傳重新排序與計算座標後的新 SequenceDiagramData 與成本明細。
 */
export function autoLayoutLifelines(
  diagram: SequenceDiagramData,
  options: LifelineLayoutOptions = {},
): LifelineLayoutResult {
  const startX = options.startX ?? DEFAULT_START_X;
  const spacing = options.spacing ?? DEFAULT_SPACING;
  const fragmentPaddingX = options.fragmentPaddingX ?? DEFAULT_FRAGMENT_PADDING_X;
  const weights = options.weights ?? DEFAULT_LAYOUT_WEIGHTS;

  const greedyOrder = buildGreedyOrder(diagram);
  const { order, cost } = optimizeByAdjacentSwap(greedyOrder, diagram, weights);

  const lifelineById = new Map<string, Lifeline>(
    diagram.lifelines.map((l) => [l.id, l]),
  );

  const updatedLifelines: Lifeline[] = order
    .map((id, index) => {
      const original = lifelineById.get(id);
      if (!original) return null;
      return {
        ...original,
        x: startX + index * spacing,
      };
    })
    .filter((l): l is Lifeline => l !== null);

  const updatedXById = new Map<string, number>(
    updatedLifelines.map((l) => [l.id, l.x]),
  );

  const updatedFragments: Fragment[] = diagram.fragments.map((fragment) => {
    if (fragment.type === 'ignore' || fragment.type === 'consider') {
      return fragment;
    }

    const participants = getFragmentLifelineIds(fragment, diagram.messages);
    const xs: number[] = [];
    for (const id of participants) {
      const x = updatedXById.get(id);
      if (x !== undefined) {
        xs.push(x);
      }
    }

    if (xs.length === 0) {
      return fragment;
    }

    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    return {
      ...fragment,
      x: minX - fragmentPaddingX,
      width: maxX - minX + fragmentPaddingX * 2,
    };
  });

  return {
    diagram: {
      ...diagram,
      lifelines: updatedLifelines,
      fragments: updatedFragments,
    },
    order,
    cost,
  };
}
