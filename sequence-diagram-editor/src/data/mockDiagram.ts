import { SequenceDiagramData } from '../types/sequenceDiagram';

/**
 * 測試資料：
 * 包含 4 個物件、6 筆訊息與 3 個 Combined Fragment（alt、par、ignore）
 */
export const mockSequenceDiagram: SequenceDiagramData = {
  id: 'diagram-auth-flow',
  title: 'User Login Sequence (Layout Editor)',
  lifelines: [
    { id: 'client', name: 'Client', x: 140, y: 32, width: 128, height: 42 },
    { id: 'gateway', name: 'API Gateway', x: 340, y: 32, width: 128, height: 42 },
    { id: 'auth', name: 'AuthService', x: 540, y: 32, width: 128, height: 42 },
    { id: 'db', name: 'Database', x: 740, y: 32, width: 128, height: 42 },
  ],
  fragments: [
    {
      id: 'frag-alt-1',
      type: 'alt',
      x: 280,
      y: 205,
      width: 520,
      height: 185,
      guard: '[credentials valid]',
      interactions: ['msg-3', 'msg-4', 'msg-5'],
      operands: [
        {
          id: 'op-alt-1',
          guard: '[credentials valid]',
          y: 205,
          interactions: ['msg-3', 'msg-4', 'msg-5'],
        },
      ],
    },
    {
      id: 'frag-par-1',
      type: 'par',
      x: 90,
      y: 410,
      width: 500,
      height: 105,
      guard: '[notify & respond]',
      interactions: ['msg-6'],
    },
    {
      id: 'frag-ignore-1',
      type: 'ignore',
      x: 90,
      y: 95,
      width: 320,
      height: 100,
      interactions: ['heartbeat', 'i3'],
    },
  ],
  messages: [
    {
      id: 'msg-1',
      fromId: 'client',
      toId: 'gateway',
      interaction: 'loginRequest(username, password)',
      y: 125,
      order: 1,
    },
    {
      id: 'msg-2',
      fromId: 'gateway',
      toId: 'auth',
      interaction: 'authenticate(username, password)',
      y: 175,
      order: 2,
    },
    {
      id: 'msg-3',
      fromId: 'auth',
      toId: 'db',
      interaction: 'findUserByUsername(username)',
      y: 260,
      order: 3,
      fragmentId: 'frag-alt-1',
    },
    {
      id: 'msg-4',
      fromId: 'db',
      toId: 'auth',
      interaction: 'userRecord(id, passwordHash)',
      y: 315,
      order: 4,
      fragmentId: 'frag-alt-1',
    },
    {
      id: 'msg-5',
      fromId: 'auth',
      toId: 'gateway',
      interaction: 'issueToken(jwt)',
      y: 365,
      order: 5,
      fragmentId: 'frag-alt-1',
    },
    {
      id: 'msg-6',
      fromId: 'gateway',
      toId: 'client',
      interaction: 'loginSuccess(200 OK, jwt)',
      y: 475,
      order: 6,
      fragmentId: 'frag-par-1',
    },
  ],
};
