import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, createReview, errorMessage, type ReviewTask } from "../api";
export function ReviewForm({
  onAccepted,
}: {
  onAccepted: (task: ReviewTask) => void;
}) {
  const [repository, setRepository] = useState("");
  const [number, setNumber] = useState("");
  const [errors, setErrors] = useState<{
    repository?: string;
    number?: string;
  }>({});
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);
  const activeRequest = useRef<AbortController | null>(null);
  const locked = useRef(false);
  const repositoryInput = useRef<HTMLInputElement>(null);
  const numberInput = useRef<HTMLInputElement>(null);
  const message = useRef<HTMLDivElement>(null);
  useEffect(() => () => activeRequest.current?.abort(), []);
  useEffect(() => {
    if (failure) message.current?.focus();
  }, [failure]);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (locked.current) return;
    const next: typeof errors = {};
    if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository.trim()))
      next.repository = "请输入 owner/repo 格式，例如 team/project。";
    if (
      !/^\d+$/.test(number) ||
      !Number.isSafeInteger(Number(number)) ||
      Number(number) < 1
    )
      next.number = "请输入大于 0 的整数 PR 编号。";
    setErrors(next);
    setFailure(null);
    if (next.repository || next.number) {
      (next.repository ? repositoryInput : numberInput).current?.focus();
      return;
    }
    locked.current = true;
    setSubmitting(true);
    const controller = new AbortController();
    activeRequest.current = controller;
    try {
      const task = await createReview(
        { repository: repository.trim(), pr_number: Number(number) },
        controller.signal,
      );
      if (!controller.signal.aborted) onAccepted(task);
    } catch (error) {
      if (!controller.signal.aborted) setFailure(error);
    } finally {
      locked.current = false;
      if (!controller.signal.aborted) setSubmitting(false);
    }
  }
  return (
    <section className="panel submit-panel" aria-labelledby="new-review-title">
      <div className="section-kicker">NEW REVIEW</div>
      <h2 id="new-review-title">发起一次审查</h2>
      <p className="muted">
        选择 GitHub 仓库和 PR，查看这次修改中值得核对的问题。
      </p>
      <form onSubmit={submit} noValidate aria-busy={submitting}>
        <label htmlFor="repository">GitHub 仓库</label>
        <input
          id="repository"
          ref={repositoryInput}
          value={repository}
          onChange={(e) => setRepository(e.target.value)}
          placeholder="owner/repo"
          autoComplete="off"
          spellCheck={false}
          disabled={submitting}
          aria-invalid={Boolean(errors.repository)}
          aria-describedby={
            errors.repository ? "repository-error" : "repository-hint"
          }
        />
        {errors.repository ? (
          <p id="repository-error" className="field-error">
            {errors.repository}
          </p>
        ) : (
          <p id="repository-hint" className="field-hint">
            填写仓库名称，不需要粘贴完整网址。
          </p>
        )}
        <label htmlFor="pr-number">Pull Request 编号</label>
        <div className="input-with-prefix">
          <span aria-hidden="true">#</span>
          <input
            id="pr-number"
            ref={numberInput}
            value={number}
            onChange={(e) => setNumber(e.target.value)}
            placeholder="例如 12"
            inputMode="numeric"
            autoComplete="off"
            disabled={submitting}
            aria-invalid={Boolean(errors.number)}
            aria-describedby={errors.number ? "number-error" : undefined}
          />
        </div>
        {errors.number && (
          <p id="number-error" className="field-error">
            {errors.number}
          </p>
        )}
        <button
          type="submit"
          className="button primary submit-button"
          disabled={submitting}
        >
          {submitting ? "正在提交…" : "开始审查"}
          <span aria-hidden="true">↗</span>
        </button>
        {submitting && (
          <p role="status" className="field-hint">
            正在等待服务受理，请勿重复提交。
          </p>
        )}
        {failure != null && (
          <div className="form-error" ref={message} tabIndex={-1} role="alert">
            <strong>
              {failure instanceof ApiError && failure.status === 501
                ? "审查服务尚未接通"
                : "提交未成功"}
            </strong>
            <p>{errorMessage(failure)}</p>
            {failure instanceof ApiError && failure.requestId && (
              <small>请求编号：{failure.requestId}</small>
            )}
          </div>
        )}
      </form>
      <div className="scope-note">
        <span className="section-kicker">审查范围</span>
        <p>
          仅支持已接入仓库中的 Python
          PR。报告提供候选问题与代码依据，由你核对后决定如何处理。
        </p>
      </div>
    </section>
  );
}
