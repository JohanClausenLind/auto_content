import {
  Tabs as AriaTabs,
  Tab as AriaTab,
  TabList as AriaTabList,
  TabPanel as AriaTabPanel,
  type TabsProps,
  type TabProps,
  type TabListProps,
  type TabPanelProps,
} from "react-aria-components";

export function Tabs(props: TabsProps) {
  return <AriaTabs {...props} className="cf-tabs" />;
}
export function TabList<T extends object>(props: TabListProps<T>) {
  return <AriaTabList {...props} className="cf-tabs__list" />;
}
export function Tab(props: TabProps) {
  return <AriaTab {...props} className="cf-tabs__tab" />;
}
export function TabPanel(props: TabPanelProps) {
  return <AriaTabPanel {...props} className="cf-tabs__panel" />;
}
