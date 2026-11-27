/**
 * Combined Fragment 類型定義
 */
export type CombinedFragmentType =
  | 'alt'
  | 'opt'
  | 'loop'
  | 'par'
  | 'ignore'
  | 'consider'
  | string;

/**
 * Lifeline（物件生命線）資料型別
 */
export interface Lifeline {
  id: string;
  name: string;
  x: number;
  y?: number;
  width?: number;
  height?: number;
}

/**
 * Message（訊息互動）資料型別
 */
export interface Message {
  id: string;
  fromId: string;
  toId: string;
  interaction: string;
  y: number;
  order?: number;
  fragmentId?: string;
}

/**
 * Combined Fragment 子區塊（Operand）預留型別
 */
export interface FragmentOperand {
  id: string;
  guard?: string;
  y?: number;
  interactions?: string[];
}

/**
 * Combined Fragment（組合片段）資料型別
 */
export interface Fragment {
  id: string;
  type: CombinedFragmentType;
  x: number;
  y: number;
  width: number;
  height: number;
  guard?: string;
  interactions?: string[];
  operands?: FragmentOperand[];
}

/**
 * 完整 Sequence Diagram 資料結構
 */
export interface SequenceDiagramData {
  id: string;
  title: string;
  lifelines: Lifeline[];
  messages: Message[];
  fragments: Fragment[];
}
