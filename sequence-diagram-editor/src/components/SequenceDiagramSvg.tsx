import React, { useCallback, useEffect, useRef, useState } from 'react';
import { getFragmentLifelineIds } from '../layout/lifelineLayout';
import {
  CombinedFragmentType,
  Fragment,
  Lifeline,
  Message,
  SequenceDiagramData,
} from '../types/sequenceDiagram';

interface SequenceDiagramSvgProps {
  diagram: SequenceDiagramData;
  width?: number;
  height?: number;
  onLifelineMove?: (lifelineId: string, nextX: number) => void;
  onMessageMove?: (messageId: string, nextY: number) => void;
  onFragmentCreate?: (fragment: Fragment) => void;
  onFragmentUpdate?: (
    fragmentId: string,
    bounds: Pick<Fragment, 'x' | 'y' | 'width' | 'height'>,
  ) => void;
  onFragmentEdit?: (fragment: Fragment) => void;
}

type ResizeHandle = 'nw' | 'ne' | 'sw' | 'se';
type CreateFragmentKind = 'ignore' | 'consider';

type ActiveDrag =
  | { kind: 'lifeline'; id: string; offset: number }
  | { kind: 'message'; id: string; offset: number }
  | {
      kind: 'fragment-move';
      id: string;
      offsetX: number;
      offsetY: number;
      width: number;
      height: number;
    }
  | {
      kind: 'fragment-resize';
      id: string;
      handle: ResizeHandle;
      initial: { x: number; y: number; width: number; height: number };
      startPt: { x: number; y: number };
    }
  | {
      kind: 'fragment-draw';
      fragmentType: CreateFragmentKind;
      startX: number;
      startY: number;
      currentX: number;
      currentY: number;
    };

interface ContextMenuState {
  x: number;
  y: number;
}

const DEFAULT_BOX_WIDTH = 128;
const DEFAULT_BOX_HEIGHT = 42;
const DEFAULT_LIFELINE_Y = 32;
const CANVAS_PADDING_X = 20;
const CANVAS_PADDING_Y = 8;
const MIN_FRAGMENT_WIDTH = 40;
const MIN_FRAGMENT_HEIGHT = 30;
const RESIZE_HANDLE_SIZE = 8;

function getFragmentColors(type: CombinedFragmentType) {
  if (type === 'ignore') {
    return {
      fill: 'rgba(245, 158, 11, 0.06)',
      stroke: '#d97706',
      badgeFill: '#fef3c7',
      textFill: '#92400e',
    };
  }
  if (type === 'consider') {
    return {
      fill: 'rgba(16, 185, 129, 0.06)',
      stroke: '#059669',
      badgeFill: '#d1fae5',
      textFill: '#065f46',
    };
  }
  return {
    fill: 'rgba(59, 130, 246, 0.04)',
    stroke: '#64748b',
    badgeFill: '#e2e8f0',
    textFill: '#0f172a',
  };
}

export const SequenceDiagramSvg: React.FC<SequenceDiagramSvgProps> = ({
  diagram,
  width = 880,
  height = 560,
  onLifelineMove,
  onMessageMove,
  onFragmentCreate,
  onFragmentUpdate,
  onFragmentEdit,
}) => {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [activeDrag, setActiveDrag] = useState<ActiveDrag | null>(null);
  const activeDragRef = useRef<ActiveDrag | null>(null);
  const lifelinesRef = useRef<Lifeline[]>(diagram.lifelines);
  lifelinesRef.current = diagram.lifelines;

  const updateActiveDrag = useCallback((next: ActiveDrag | null) => {
    activeDragRef.current = next;
    setActiveDrag(next);
  }, []);

  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const [drawMode, setDrawMode] = useState<CreateFragmentKind | null>(null);

  const lifelineMap = new Map<string, Lifeline>(
    diagram.lifelines.map((lifeline) => [lifeline.id, lifeline]),
  );

  const svgWidth = Math.max(width, diagram.lifelines.length * 200 + 80);
  const lifelineBottomY = height - 28;

  const clientToSvgPoint = useCallback(
    (clientX: number, clientY: number): { x: number; y: number } | null => {
      const svg = svgRef.current;
      if (!svg) return null;

      const ctm = svg.getScreenCTM();
      if (ctm) {
        const point = svg.createSVGPoint();
        point.x = clientX;
        point.y = clientY;
        const transformed = point.matrixTransform(ctm.inverse());
        return { x: transformed.x, y: transformed.y };
      }

      const rect = svg.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return null;
      return {
        x: ((clientX - rect.left) / rect.width) * svgWidth,
        y: ((clientY - rect.top) / rect.height) * height,
      };
    },
    [svgWidth, height],
  );

  const handleContextMenu = (e: React.MouseEvent<SVGSVGElement>) => {
    e.preventDefault();
    const clampedX = Math.min(e.clientX, window.innerWidth - 204);
    const clampedY = Math.min(e.clientY, window.innerHeight - 96);
    setContextMenu({ x: Math.max(8, clampedX), y: Math.max(8, clampedY) });
  };

  const startCreateMode = (kind: CreateFragmentKind) => {
    setContextMenu(null);
    setDrawMode(kind);
  };

  const handleSvgMouseDown = (e: React.MouseEvent<SVGSVGElement>) => {
    if (contextMenu) {
      setContextMenu(null);
    }

    if (!drawMode || e.button !== 0) return;
    e.preventDefault();

    const pt = clientToSvgPoint(e.clientX, e.clientY);
    if (!pt) return;

    const clampedX = Math.max(8, Math.min(svgWidth - 8, pt.x));
    const clampedY = Math.max(8, Math.min(height - 8, pt.y));

    updateActiveDrag({
      kind: 'fragment-draw',
      fragmentType: drawMode,
      startX: clampedX,
      startY: clampedY,
      currentX: clampedX,
      currentY: clampedY,
    });
  };

  const handleLifelineMouseDown = (
    e: React.MouseEvent<SVGGElement>,
    lifeline: Lifeline,
  ) => {
    if (drawMode || e.button !== 0 || !onLifelineMove) return;
    e.preventDefault();
    e.stopPropagation();
    setContextMenu(null);

    const pt = clientToSvgPoint(e.clientX, e.clientY);
    if (!pt) return;

    updateActiveDrag({
      kind: 'lifeline',
      id: lifeline.id,
      offset: pt.x - lifeline.x,
    });
  };

  const handleMessageMouseDown = (
    e: React.MouseEvent<SVGGElement>,
    message: Message,
  ) => {
    if (drawMode || e.button !== 0 || !onMessageMove) return;
    e.preventDefault();
    e.stopPropagation();
    setContextMenu(null);

    const pt = clientToSvgPoint(e.clientX, e.clientY);
    if (!pt) return;

    updateActiveDrag({
      kind: 'message',
      id: message.id,
      offset: pt.y - message.y,
    });
  };

  const handleFragmentMouseDown = (
    e: React.MouseEvent<SVGGElement>,
    fragment: Fragment,
  ) => {
    if (drawMode || e.button !== 0 || !onFragmentUpdate) return;
    if (fragment.type !== 'ignore' && fragment.type !== 'consider') return;
    e.preventDefault();
    e.stopPropagation();
    setContextMenu(null);

    const pt = clientToSvgPoint(e.clientX, e.clientY);
    if (!pt) return;

    updateActiveDrag({
      kind: 'fragment-move',
      id: fragment.id,
      offsetX: pt.x - fragment.x,
      offsetY: pt.y - fragment.y,
      width: fragment.width,
      height: fragment.height,
    });
  };

  const handleFragmentResizeMouseDown = (
    e: React.MouseEvent<SVGRectElement>,
    fragment: Fragment,
    handle: ResizeHandle,
  ) => {
    if (drawMode || e.button !== 0 || !onFragmentUpdate) return;
    e.preventDefault();
    e.stopPropagation();
    setContextMenu(null);

    const pt = clientToSvgPoint(e.clientX, e.clientY);
    if (!pt) return;

    updateActiveDrag({
      kind: 'fragment-resize',
      id: fragment.id,
      handle,
      initial: {
        x: fragment.x,
        y: fragment.y,
        width: fragment.width,
        height: fragment.height,
      },
      startPt: pt,
    });
  };

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setContextMenu(null);
        setDrawMode(null);
        updateActiveDrag(null);
      }
    };
    const handleWindowMouseDown = () => {
      setContextMenu(null);
    };

    window.addEventListener('keydown', handleKeyDown);
    window.addEventListener('mousedown', handleWindowMouseDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
      window.removeEventListener('mousedown', handleWindowMouseDown);
    };
  }, [updateActiveDrag]);

  const isDragging = activeDrag !== null;

  useEffect(() => {
    if (!isDragging) return;

    const handleMouseMove = (e: MouseEvent) => {
      const currentDrag = activeDragRef.current;
      if (!currentDrag) return;

      const pt = clientToSvgPoint(e.clientX, e.clientY);
      if (!pt) return;

      if (currentDrag.kind === 'lifeline' && onLifelineMove) {
        const target = lifelinesRef.current.find((l) => l.id === currentDrag.id);
        const boxWidth = target?.width ?? DEFAULT_BOX_WIDTH;
        const minX = boxWidth / 2 + CANVAS_PADDING_X;
        const maxX = svgWidth - boxWidth / 2 - CANVAS_PADDING_X;
        const rawX = pt.x - currentDrag.offset;
        const clampedX = Math.round(Math.max(minX, Math.min(maxX, rawX)));
        onLifelineMove(currentDrag.id, clampedX);
      } else if (currentDrag.kind === 'message' && onMessageMove) {
        const rawY = pt.y - currentDrag.offset;
        onMessageMove(currentDrag.id, Math.round(rawY));
      } else if (currentDrag.kind === 'fragment-move' && onFragmentUpdate) {
        const maxX = svgWidth - currentDrag.width - CANVAS_PADDING_Y;
        const maxY = height - currentDrag.height - CANVAS_PADDING_Y;
        const nextX = Math.round(
          Math.max(CANVAS_PADDING_Y, Math.min(maxX, pt.x - currentDrag.offsetX)),
        );
        const nextY = Math.round(
          Math.max(CANVAS_PADDING_Y, Math.min(maxY, pt.y - currentDrag.offsetY)),
        );
        onFragmentUpdate(currentDrag.id, {
          x: nextX,
          y: nextY,
          width: currentDrag.width,
          height: currentDrag.height,
        });
      } else if (currentDrag.kind === 'fragment-resize' && onFragmentUpdate) {
        const dx = pt.x - currentDrag.startPt.x;
        const dy = pt.y - currentDrag.startPt.y;
        const { x, y, width: w, height: h } = currentDrag.initial;
        const right = x + w;
        const bottom = y + h;

        let nextX = x;
        let nextY = y;
        let nextW = w;
        let nextH = h;

        if (currentDrag.handle.includes('e')) {
          const maxW = svgWidth - CANVAS_PADDING_Y - x;
          nextW = Math.max(MIN_FRAGMENT_WIDTH, Math.min(maxW, w + dx));
        }
        if (currentDrag.handle.includes('s')) {
          const maxH = height - CANVAS_PADDING_Y - y;
          nextH = Math.max(MIN_FRAGMENT_HEIGHT, Math.min(maxH, h + dy));
        }
        if (currentDrag.handle.includes('w')) {
          const maxX = right - MIN_FRAGMENT_WIDTH;
          nextX = Math.max(CANVAS_PADDING_Y, Math.min(maxX, x + dx));
          nextW = right - nextX;
        }
        if (currentDrag.handle.includes('n')) {
          const maxY = bottom - MIN_FRAGMENT_HEIGHT;
          nextY = Math.max(CANVAS_PADDING_Y, Math.min(maxY, y + dy));
          nextH = bottom - nextY;
        }

        onFragmentUpdate(currentDrag.id, {
          x: Math.round(nextX),
          y: Math.round(nextY),
          width: Math.round(nextW),
          height: Math.round(nextH),
        });
      } else if (currentDrag.kind === 'fragment-draw') {
        const clampedX = Math.max(8, Math.min(svgWidth - 8, pt.x));
        const clampedY = Math.max(8, Math.min(height - 8, pt.y));
        const nextDraw: ActiveDrag = {
          ...currentDrag,
          currentX: clampedX,
          currentY: clampedY,
        };
        updateActiveDrag(nextDraw);
      }
    };

    const handleMouseUp = () => {
      const currentDrag = activeDragRef.current;
      if (currentDrag?.kind === 'fragment-draw') {
        const x = Math.min(currentDrag.startX, currentDrag.currentX);
        const y = Math.min(currentDrag.startY, currentDrag.currentY);
        const rectW = Math.abs(currentDrag.currentX - currentDrag.startX);
        const rectH = Math.abs(currentDrag.currentY - currentDrag.startY);

        if (
          rectW >= MIN_FRAGMENT_WIDTH &&
          rectH >= MIN_FRAGMENT_HEIGHT &&
          onFragmentCreate
        ) {
          onFragmentCreate({
            id: `frag-${currentDrag.fragmentType}-${Date.now()}`,
            type: currentDrag.fragmentType,
            x: Math.round(x),
            y: Math.round(y),
            width: Math.round(rectW),
            height: Math.round(rectH),
            interactions: [],
          });
        }
        setDrawMode(null);
      }
      updateActiveDrag(null);
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);

    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [
    isDragging,
    svgWidth,
    height,
    clientToSvgPoint,
    onLifelineMove,
    onMessageMove,
    onFragmentCreate,
    onFragmentUpdate,
    updateActiveDrag,
  ]);

  const svgCursorClass = drawMode
    ? 'is-drawing'
    : activeDrag?.kind === 'lifeline' || activeDrag?.kind === 'fragment-move'
      ? 'is-dragging-x'
      : activeDrag?.kind === 'message'
        ? 'is-dragging-y'
        : '';

  const drawPreviewRect =
    activeDrag?.kind === 'fragment-draw'
      ? {
          type: activeDrag.fragmentType,
          x: Math.min(activeDrag.startX, activeDrag.currentX),
          y: Math.min(activeDrag.startY, activeDrag.currentY),
          width: Math.abs(activeDrag.currentX - activeDrag.startX),
          height: Math.abs(activeDrag.currentY - activeDrag.startY),
        }
      : null;

  return (
    <div className="sd-canvas-wrapper">
      {drawMode && (
        <div className="sd-draw-banner">
          <span>
            繪製模式（<strong>{drawMode}</strong>）：請在畫布上按住滑鼠左鍵拖曳框出區域（最小 {MIN_FRAGMENT_WIDTH}×{MIN_FRAGMENT_HEIGHT}，按 Esc 取消）
          </span>
          <button
            type="button"
            className="sd-draw-cancel"
            onClick={() => setDrawMode(null)}
          >
            取消
          </button>
        </div>
      )}

      <svg
        ref={svgRef}
        className={`sd-svg ${svgCursorClass}`}
        viewBox={`0 0 ${svgWidth} ${height}`}
        width="100%"
        height="100%"
        role="img"
        aria-label={diagram.title}
        onContextMenu={handleContextMenu}
        onMouseDown={handleSvgMouseDown}
      >
        <defs>
          <marker
            id="sd-arrowhead"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M 0 1 L 10 5 L 0 9 z" fill="#1e293b" />
          </marker>
          <marker
            id="sd-arrowhead-active"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M 0 1 L 10 5 L 0 9 z" fill="#2563eb" />
          </marker>
        </defs>

        {/* 1. Combined Fragments */}
        <g className="sd-fragments">
          {diagram.fragments.map((fragment) => {
            const isEditableFragment =
              fragment.type === 'ignore' || fragment.type === 'consider';

            const participants = isEditableFragment
              ? new Set<string>()
              : getFragmentLifelineIds(fragment, diagram.messages);
            const participantXs: number[] = [];
            for (const id of participants) {
              const lifeline = lifelineMap.get(id);
              if (lifeline) {
                participantXs.push(lifeline.x);
              }
            }

            const fragX =
              participantXs.length > 0
                ? Math.min(...participantXs) - 76
                : fragment.x;
            const fragWidth =
              participantXs.length > 0
                ? Math.max(...participantXs) - Math.min(...participantXs) + 152
                : fragment.width;

            const isActiveFragment =
              (activeDrag?.kind === 'fragment-move' ||
                activeDrag?.kind === 'fragment-resize') &&
              activeDrag.id === fragment.id;

            const colors = getFragmentColors(fragment.type);
            const badgeWidth = Math.max(54, fragment.type.length * 9 + 24);
            const badgeHeight = 24;
            const interactionsSummary =
              isEditableFragment &&
              fragment.interactions &&
              fragment.interactions.length > 0
                ? `{${fragment.interactions.join(', ')}}`
                : null;

            const halfHandle = RESIZE_HANDLE_SIZE / 2;
            const handles: { key: ResizeHandle; hx: number; hy: number }[] = [
              { key: 'nw', hx: fragX - halfHandle, hy: fragment.y - halfHandle },
              {
                key: 'ne',
                hx: fragX + fragWidth - halfHandle,
                hy: fragment.y - halfHandle,
              },
              {
                key: 'sw',
                hx: fragX - halfHandle,
                hy: fragment.y + fragment.height - halfHandle,
              },
              {
                key: 'se',
                hx: fragX + fragWidth - halfHandle,
                hy: fragment.y + fragment.height - halfHandle,
              },
            ];

            return (
              <g
                key={fragment.id}
                className={`sd-fragment ${isEditableFragment ? 'is-editable' : ''} ${isActiveFragment ? 'is-active' : ''}`}
                onMouseDown={(e) => handleFragmentMouseDown(e, fragment)}
              >
                <rect
                  x={fragX}
                  y={fragment.y}
                  width={fragWidth}
                  height={fragment.height}
                  rx={4}
                  fill={colors.fill}
                  stroke={isActiveFragment ? '#2563eb' : colors.stroke}
                  strokeWidth={isActiveFragment ? 2 : 1.5}
                />
                <g
                  className={isEditableFragment ? 'sd-fragment-badge-btn' : ''}
                  onMouseDown={(e) => {
                    if (isEditableFragment) {
                      e.stopPropagation();
                    }
                  }}
                  onClick={(e) => {
                    if (isEditableFragment && onFragmentEdit) {
                      e.stopPropagation();
                      onFragmentEdit(fragment);
                    }
                  }}
                >
                  <path
                    d={`M ${fragX} ${fragment.y}
                        H ${fragX + badgeWidth}
                        V ${fragment.y + badgeHeight - 6}
                        L ${fragX + badgeWidth - 6} ${fragment.y + badgeHeight}
                        H ${fragX}
                        Z`}
                    fill={colors.badgeFill}
                    stroke={isActiveFragment ? '#2563eb' : colors.stroke}
                    strokeWidth={isActiveFragment ? 2 : 1.5}
                  />
                  <text
                    x={fragX + 10}
                    y={fragment.y + 16}
                    fontSize={12}
                    fontWeight={700}
                    fill={colors.textFill}
                  >
                    {fragment.type}
                  </text>
                </g>
                {fragment.guard && (
                  <text
                    x={fragX + badgeWidth + 10}
                    y={fragment.y + 16}
                    fontSize={12}
                    fill="#475569"
                  >
                    {fragment.guard}
                  </text>
                )}
                {interactionsSummary && (
                  <text
                    x={fragX + badgeWidth + 10}
                    y={fragment.y + 16}
                    fontSize={11.5}
                    fill={colors.textFill}
                  >
                    {interactionsSummary}
                  </text>
                )}

                {isEditableFragment &&
                  handles.map((h) => (
                    <rect
                      key={h.key}
                      className={`sd-resize-handle handle-${h.key}`}
                      x={h.hx}
                      y={h.hy}
                      width={RESIZE_HANDLE_SIZE}
                      height={RESIZE_HANDLE_SIZE}
                      rx={2}
                      fill="#ffffff"
                      stroke={colors.stroke}
                      strokeWidth={1.5}
                      onMouseDown={(e) =>
                        handleFragmentResizeMouseDown(e, fragment, h.key)
                      }
                    />
                  ))}
              </g>
            );
          })}
        </g>

        {/* 2. Lifelines */}
        <g className="sd-lifelines">
          {diagram.lifelines.map((lifeline) => {
            const boxWidth = lifeline.width ?? DEFAULT_BOX_WIDTH;
            const boxHeight = lifeline.height ?? DEFAULT_BOX_HEIGHT;
            const boxY = lifeline.y ?? DEFAULT_LIFELINE_Y;
            const boxX = lifeline.x - boxWidth / 2;
            const isActive =
              activeDrag?.kind === 'lifeline' && activeDrag.id === lifeline.id;

            return (
              <g key={lifeline.id} className="sd-lifeline">
                <line
                  x1={lifeline.x}
                  y1={boxY + boxHeight}
                  x2={lifeline.x}
                  y2={lifelineBottomY}
                  stroke={isActive ? '#2563eb' : '#94a3b8'}
                  strokeWidth={isActive ? 2 : 1.5}
                  strokeDasharray="6 6"
                />
                <g
                  className={`sd-lifeline-header ${isActive ? 'is-active' : ''}`}
                  onMouseDown={(e) => handleLifelineMouseDown(e, lifeline)}
                >
                  <rect
                    x={boxX}
                    y={boxY}
                    width={boxWidth}
                    height={boxHeight}
                    rx={6}
                    fill={isActive ? '#eff6ff' : '#ffffff'}
                    stroke={isActive ? '#2563eb' : '#334155'}
                    strokeWidth={isActive ? 2 : 1.75}
                  />
                  <text
                    x={lifeline.x}
                    y={boxY + boxHeight / 2 + 5}
                    textAnchor="middle"
                    fontSize={14}
                    fontWeight={600}
                    fill="#0f172a"
                  >
                    {lifeline.name}
                  </text>
                </g>
              </g>
            );
          })}
        </g>

        {/* 3. Messages */}
        <g className="sd-messages">
          {diagram.messages.map((message) => {
            const from = lifelineMap.get(message.fromId);
            const to = lifelineMap.get(message.toId);
            if (!from || !to) return null;

            const isSelfMessage = from.id === to.id;
            const isActive =
              activeDrag?.kind === 'message' && activeDrag.id === message.id;
            const strokeColor = isActive ? '#2563eb' : '#1e293b';
            const markerUrl = isActive
              ? 'url(#sd-arrowhead-active)'
              : 'url(#sd-arrowhead)';

            if (isSelfMessage) {
              const loopWidth = 42;
              const loopHeight = 24;
              const points = `${from.x},${message.y} ${from.x + loopWidth},${message.y} ${from.x + loopWidth},${message.y + loopHeight} ${from.x},${message.y + loopHeight}`;

              return (
                <g
                  key={message.id}
                  className={`sd-message ${isActive ? 'is-active' : ''}`}
                  onMouseDown={(e) => handleMessageMouseDown(e, message)}
                >
                  <polyline
                    points={points}
                    fill="none"
                    stroke="transparent"
                    strokeWidth={14}
                  />
                  <polyline
                    points={points}
                    fill="none"
                    stroke={strokeColor}
                    strokeWidth={isActive ? 2.25 : 1.75}
                    markerEnd={markerUrl}
                  />
                  <text
                    x={from.x + loopWidth + 8}
                    y={message.y + 4}
                    fontSize={12.5}
                    fontWeight={isActive ? 600 : 500}
                    fill={isActive ? '#1d4ed8' : '#0f172a'}
                  >
                    {message.interaction}
                  </text>
                </g>
              );
            }

            const midX = (from.x + to.x) / 2;

            return (
              <g
                key={message.id}
                className={`sd-message ${isActive ? 'is-active' : ''}`}
                onMouseDown={(e) => handleMessageMouseDown(e, message)}
              >
                <line
                  x1={from.x}
                  y1={message.y}
                  x2={to.x}
                  y2={message.y}
                  stroke="transparent"
                  strokeWidth={16}
                />
                <line
                  x1={from.x}
                  y1={message.y}
                  x2={to.x}
                  y2={message.y}
                  stroke={strokeColor}
                  strokeWidth={isActive ? 2.25 : 1.75}
                  markerEnd={markerUrl}
                />
                <text
                  x={midX}
                  y={message.y - 8}
                  textAnchor="middle"
                  fontSize={12.5}
                  fontWeight={isActive ? 600 : 500}
                  fill={isActive ? '#1d4ed8' : '#0f172a'}
                  paintOrder="stroke"
                  stroke="#ffffff"
                  strokeWidth={4}
                  strokeLinejoin="round"
                >
                  {message.interaction}
                </text>
              </g>
            );
          })}
        </g>

        {/* 4. 繪製模式預覽矩形 */}
        {drawPreviewRect && (
          <g className="sd-draw-preview" pointerEvents="none">
            <rect
              x={drawPreviewRect.x}
              y={drawPreviewRect.y}
              width={drawPreviewRect.width}
              height={drawPreviewRect.height}
              rx={4}
              fill={getFragmentColors(drawPreviewRect.type).fill}
              stroke={getFragmentColors(drawPreviewRect.type).stroke}
              strokeWidth={1.75}
              strokeDasharray="6 4"
            />
            {drawPreviewRect.width >= 40 && drawPreviewRect.height >= 24 && (
              <text
                x={drawPreviewRect.x + 10}
                y={drawPreviewRect.y + 18}
                fontSize={12}
                fontWeight={700}
                fill={getFragmentColors(drawPreviewRect.type).textFill}
              >
                {drawPreviewRect.type}
              </text>
            )}
          </g>
        )}
      </svg>

      {contextMenu && (
        <div
          className="sd-context-menu"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          onMouseDown={(e) => e.stopPropagation()}
        >
          <button
            type="button"
            className="sd-context-menu-item"
            onClick={() => startCreateMode('ignore')}
          >
            Create Ignore Block
          </button>
          <button
            type="button"
            className="sd-context-menu-item"
            onClick={() => startCreateMode('consider')}
          >
            Create Consider Block
          </button>
        </div>
      )}
    </div>
  );
};
