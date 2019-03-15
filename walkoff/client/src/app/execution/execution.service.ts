import { Injectable } from '@angular/core';
import 'rxjs/add/operator/toPromise';
import { plainToClass, classToPlain } from 'class-transformer';
import { HttpClient } from '@angular/common/http';

import { WorkflowStatus } from '../models/execution/workflowStatus';
import { Playbook } from '../models/playbook/playbook';
import { Workflow } from '../models/playbook/workflow';
import { EnvironmentVariable } from '../models/playbook/environmentVariable';
import { UtilitiesService } from '../utilities.service';

@Injectable({
	providedIn: 'root'
})
export class ExecutionService {
	constructor (private http: HttpClient, private utils: UtilitiesService) {}

	/**
	 * Asyncronously adds a workflow (by ID) to the queue to be executed.
	 * Returns the new workflow status for the workflow execution.
	 * @param workflowId Workflow Id to queue
	 */
	addWorkflowToQueue(workflow_id: string, variables: EnvironmentVariable[] = []): Promise<WorkflowStatus> {
		let data: any = { workflow_id };
		if (variables.length > 0) data.environment_variables = classToPlain(variables);

		return this.http.post('walkoffapi/workflowqueue', data)
			.toPromise()
			.then((data: object) => plainToClass(WorkflowStatus, data))
			.catch(this.utils.handleResponseError);
	}

	/**
	 * For a given executing workflow, asyncronously perform some action to change its status.
	 * Only returns success
	 * @param workflowId Workflow ID to act upon
	 * @param action Action to take (e.g. abort, resume, pause)
	 */
	performWorkflowStatusAction(workflowId: string, action: string): Promise<void> {
		return this.http.patch('walkoffapi/workflowqueue', { execution_id: workflowId, status: action })
			.toPromise()
			.then(() => null)
			.catch(this.utils.handleResponseError);
	}

	/**
	 * Asyncronously gets an array of workflow statuses from the server.
	 */
	getAllWorkflowStatuses(): Promise<WorkflowStatus[]> {
		return this.utils.paginateAll<WorkflowStatus>(this.getWorkflowStatuses.bind(this));
	}

	/**
	 * Asyncronously gets an array of workflow statuses from the server.
	 */
	getWorkflowStatuses(page: number = 1): Promise<WorkflowStatus[]> {
		return this.http.get(`walkoffapi/workflowqueue?page=${ page }`)
			.toPromise()
			.then((data: object[]) => plainToClass(WorkflowStatus, data))
			.catch(this.utils.handleResponseError);
	}

	/**
	 * Asyncronously gets the full information for a given workflow status, including action statuses.
	 * @param workflowExecutionId Workflow Status to query
	 */
	getWorkflowStatus(workflowExecutionId: string): Promise<WorkflowStatus> {
		return this.http.get(`walkoffapi/workflowqueue/${workflowExecutionId}`)
			.toPromise()
			.then((data: object) => plainToClass(WorkflowStatus, data))
			.catch(this.utils.handleResponseError);
	}

	/**
	 * Asyncryonously gets arrays of all playbooks and workflows (id, name pairs only).
	 */
	getPlaybooks(): Promise<Playbook[]> {
		return this.http.get('/walkoffapi/playbooks')
			.toPromise()
			.then((data: object[]) => plainToClass(Playbook, data))
			.catch(this.utils.handleResponseError);
	}

	/**
	 * Loads the data of a given workflow under a given playbook.
	 * @param workflowId ID of the workflow to load
	 */
	loadWorkflow(workflowId: string): Promise<Workflow> {
		return this.http.get(`/walkoffapi/workflows/${workflowId}`)
			.toPromise()
			.then((data: object) => plainToClass(Workflow, data))
			.catch(this.utils.handleResponseError);
	}

	async getLatestExecution(workflowId: string): Promise<WorkflowStatus> {
		const workflowStatuses = await this.getAllWorkflowStatuses();
		const workflowStatus = workflowStatuses.filter(status => status.workflow_id = workflowId && status.completed_at_local).find(e => !!e);
		return this.getWorkflowStatus(workflowStatus.execution_id);
	}
}
