import { defineStore } from 'pinia'

export const useAppStore = defineStore('app', {
  state: () => ({
    token: localStorage.getItem('token') || '',
    user: JSON.parse(localStorage.getItem('user') || '{}'),
    currentDomainId: localStorage.getItem('currentDomainId') || '',
    currentDomainName: localStorage.getItem('currentDomainName') || '',
    sidebarCollapsed: false
  }),
  actions: {
    setToken(token: string) {
      this.token = token
      localStorage.setItem('token', token)
    },
    setUser(user: any) {
      this.user = user
      localStorage.setItem('user', JSON.stringify(user))
    },
    setCurrentDomain(domainId: string, domainName: string) {
      this.currentDomainId = domainId
      this.currentDomainName = domainName
      localStorage.setItem('currentDomainId', domainId)
      localStorage.setItem('currentDomainName', domainName)
    },
    logout() {
      this.token = ''
      this.user = {}
      this.currentDomainId = ''
      this.currentDomainName = ''
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      localStorage.removeItem('currentDomainId')
      localStorage.removeItem('currentDomainName')
    },
    toggleSidebar() {
      this.sidebarCollapsed = !this.sidebarCollapsed
    }
  }
})
