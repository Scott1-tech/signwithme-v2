import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';

export function Layout() {
  const { user, logout, isAtLeast } = useAuth();
  const navigate = useNavigate();

  return (
    <div className="app">
      <header className="app__header">
        <Link to="/" className="app__brand">
          Contract Signing
        </Link>
        <nav className="app__nav">
          <NavLink to="/" end>
            Dashboard
          </NavLink>
          <NavLink to="/contracts/new">Upload contract</NavLink>
          <NavLink to="/signatures">My signatures</NavLink>
          {isAtLeast('admin') && <NavLink to="/templates">Templates</NavLink>}
          {isAtLeast('reviewer') && <NavLink to="/review">Review queue</NavLink>}
          <NavLink to="/account">Password</NavLink>
        </nav>
        <div className="app__user">
          {user && (
            <>
              <span className="app__user-name">
                {user.full_name ?? user.email} <em>{user.role}</em>
              </span>
              <button
                type="button"
                className="button button--ghost"
                onClick={() => {
                  logout();
                  navigate('/login');
                }}
              >
                Sign out
              </button>
            </>
          )}
        </div>
      </header>
      <main className="app__main">
        <Outlet />
      </main>
    </div>
  );
}
