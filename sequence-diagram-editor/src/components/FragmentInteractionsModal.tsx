import React, { useEffect, useState } from 'react';
import { Fragment, Message } from '../types/sequenceDiagram';

interface FragmentInteractionsModalProps {
  fragment: Fragment;
  messages: Message[];
  onSave: (fragmentId: string, interactions: string[]) => void;
  onClose: () => void;
}

export const FragmentInteractionsModal: React.FC<
  FragmentInteractionsModalProps
> = ({ fragment, messages, onSave, onClose }) => {
  const [draftInteractions, setDraftInteractions] = useState<string[]>(
    () => fragment.interactions ?? [],
  );
  const [manualInput, setManualInput] = useState('');
  const [selectedMessageValue, setSelectedMessageValue] = useState<string>(
    () => messages[0]?.interaction ?? '',
  );

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const addInteraction = (rawValue: string) => {
    const trimmed = rawValue.trim();
    if (!trimmed) return;
    setDraftInteractions((prev) =>
      prev.includes(trimmed) ? prev : [...prev, trimmed],
    );
  };

  const handleAddManual = (e: React.FormEvent) => {
    e.preventDefault();
    if (!manualInput.trim()) return;
    addInteraction(manualInput);
    setManualInput('');
  };

  const handleAddFromMessage = () => {
    if (!selectedMessageValue) return;
    addInteraction(selectedMessageValue);
  };

  const handleRemove = (target: string) => {
    setDraftInteractions((prev) => prev.filter((item) => item !== target));
  };

  const handleSave = () => {
    onSave(fragment.id, draftInteractions);
  };

  return (
    <div
      className="sd-modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="fragment-modal-title"
      onMouseDown={onClose}
    >
      <div className="sd-modal" onMouseDown={(e) => e.stopPropagation()}>
        <div className="sd-modal-header">
          <div>
            <span className={`sd-modal-type-badge type-${fragment.type}`}>
              {fragment.type}
            </span>
            <h2 id="fragment-modal-title">編輯 Interactions 清單</h2>
            <p className="sd-modal-subtitle">Fragment ID：{fragment.id}</p>
          </div>
          <button
            type="button"
            className="sd-modal-close-icon"
            aria-label="關閉"
            onClick={onClose}
          >
            ✕
          </button>
        </div>

        <div className="sd-modal-body">
          <div className="sd-modal-section">
            <label className="sd-modal-label">目前的 Interactions</label>
            {draftInteractions.length === 0 ? (
              <div className="sd-modal-empty">尚未指定任何 interaction</div>
            ) : (
              <ul className="sd-interaction-list">
                {draftInteractions.map((item) => (
                  <li key={item} className="sd-interaction-item">
                    <span>{item}</span>
                    <button
                      type="button"
                      className="sd-remove-btn"
                      onClick={() => handleRemove(item)}
                    >
                      移除
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="sd-modal-section">
            <label className="sd-modal-label" htmlFor="message-select">
              從現有 Messages 挑選加入
            </label>
            <div className="sd-modal-row">
              <select
                id="message-select"
                className="sd-modal-select"
                value={selectedMessageValue}
                onChange={(e) => setSelectedMessageValue(e.target.value)}
              >
                {messages.map((msg) => (
                  <option key={msg.id} value={msg.interaction}>
                    {msg.interaction} ({msg.id})
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="sd-secondary-btn"
                onClick={handleAddFromMessage}
                disabled={!selectedMessageValue}
              >
                加入
              </button>
            </div>
          </div>

          <form className="sd-modal-section" onSubmit={handleAddManual}>
            <label className="sd-modal-label" htmlFor="manual-interaction-input">
              手動輸入 Interaction ID / 名稱
            </label>
            <div className="sd-modal-row">
              <input
                id="manual-interaction-input"
                type="text"
                className="sd-modal-input"
                placeholder="例如：i3、i7 或 heartbeat"
                value={manualInput}
                onChange={(e) => setManualInput(e.target.value)}
              />
              <button
                type="submit"
                className="sd-secondary-btn"
                disabled={!manualInput.trim()}
              >
                新增
              </button>
            </div>
          </form>
        </div>

        <div className="sd-modal-footer">
          <button type="button" className="sd-cancel-btn" onClick={onClose}>
            取消 / 關閉
          </button>
          <button type="button" className="sd-save-btn" onClick={handleSave}>
            儲存修改（Save）
          </button>
        </div>
      </div>
    </div>
  );
};
