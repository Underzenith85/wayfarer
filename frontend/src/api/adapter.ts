/** Read-only presentation boundary. The server owns all game rules and authorization.
 * Live DTO validation and HTTP transport belong to the versioned API integration.
 */
export interface CampaignOverview {
  id: string;
  name: string;
  scene: string;
  narration: string;
}
export interface CampaignAdapter {
  getCurrentCampaign(signal: AbortSignal): Promise<CampaignOverview | null>;
}
/** An unconfigured shell is empty, never a fabricated playable campaign. */
export const unconfiguredAdapter: CampaignAdapter = {
  getCurrentCampaign: () => Promise.resolve(null),
};
