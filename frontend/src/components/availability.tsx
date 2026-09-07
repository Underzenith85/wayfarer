import { providerBanner } from "../presentation/availability";
/**
 * The one place a shell states that no AI provider is connected. Controls that
 * the condition blocks never restate it; they carry only their own reason. The
 * condition holds for the whole session, so this is standing text, not a live
 * region competing with the status messages around it.
 */
export function ProviderBanner() {
  return (
    <div className="availability-banner">
      <p>{providerBanner.summary}</p>
      <details>
        <summary>{providerBanner.disclosure}</summary>
        <ul>
          {providerBanner.affected.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
        <p>{providerBanner.retained}</p>
      </details>
    </div>
  );
}
