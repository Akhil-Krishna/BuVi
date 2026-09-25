export type Notification = {
  id: string;
  template_key: string;
  title: string;
  body: string;
  status: string;
  created_at: string;
  read_at: string | null;
};
