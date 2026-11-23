import type { ReactNode } from "react";
import { ApiError, errorMessage } from "../api";
export function Loading({ children = "正在加载…" }: { children?: string }) {
  return (
    <div className="feedback loading" role="status">
      <span className="spinner" />
      {children}
    </div>
  );
}
export function ErrorState({
  error,
  retry,
}: {
  error: unknown;
  retry?: () => void;
}) {
  return (
    <div className="feedback error-state" role="alert">
      <p className="feedback-title">暂时无法加载</p>
      <p>{errorMessage(error)}</p>
      {error instanceof ApiError && error.requestId && (
        <p className="request-id">请求编号：{error.requestId}</p>
      )}
      {retry && (
        <button className="button secondary" onClick={retry}>
          重新加载
        </button>
      )}
    </div>
  );
}
export function EmptyState({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="feedback empty-state">
      <span className="empty-mark" aria-hidden="true">
        —
      </span>
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
