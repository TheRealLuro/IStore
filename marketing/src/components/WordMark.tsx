type Props = { large?: boolean };

export default function WordMark({ large = false }: Props) {
  return (
    <span className={`wordmark${large ? " wordmark--lg" : ""}`} aria-label="neuthek">
      <span className="wordmark__mark" aria-hidden />
      <span>neuthek</span>
    </span>
  );
}
